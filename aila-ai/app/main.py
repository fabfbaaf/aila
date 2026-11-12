"""HTTP 接口：
- POST /chat：桌面端发来对话历史，流式返回文字、表情、动作和进度事件（SSE）
- POST /tts：一句话转语音（WAV）
- WS /voice：麦克风音频进来，唤醒、说话开始/结束、识别结果出去
"""

import asyncio
import json
import logging
import os
import re
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Any, Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .agent import run_agent
from .config import load_settings
from .llm import ChatModel, Message, MockModel, OpenAICompatibleModel, classify_error
from .persona import SYSTEM_PROMPT
from .tags import Event
from .tools import Toolbox
from .tools.search import BochaSearch, MockSearch, SearchProvider, TavilySearch
from .voice import float_to_wav, pcm16_to_float

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("aila")

settings = load_settings()
# 搜索和读网页共用一个连接池
http_client = httpx.AsyncClient()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # 语音模型加载要几秒，放到后台，文字对话不用等
    preload = asyncio.gather(asyncio.to_thread(get_ears), asyncio.to_thread(get_voice)) if settings.voice else None
    yield
    if preload:
        await preload
    await http_client.aclose()


app = FastAPI(title="艾拉 AI 服务", version="0.3.0", lifespan=lifespan)


@lru_cache
def get_model() -> ChatModel:
    if settings.mock:
        log.warning("没有配置 LLM_API_KEY，使用假模型（只会几句固定回复）")
        return MockModel()
    log.info("大模型：%s（%s）", settings.llm_model, settings.llm_base_url)
    return OpenAICompatibleModel(settings)


@lru_cache
def get_toolbox() -> Toolbox:
    search: SearchProvider | None = None
    if settings.search_api_key:
        providers = {"bocha": BochaSearch, "tavily": TavilySearch}
        if settings.search_provider not in providers:
            raise ValueError(f"SEARCH_PROVIDER 只能是 {' / '.join(providers)}")
        search = providers[settings.search_provider](settings.search_api_key, http_client)
    elif settings.mock:
        search = MockSearch()  # 假模型配假搜索，整条链路不联网也能调
    log.info("联网搜索：%s", search.name if search else "未配置（只能读用户给的链接）")
    return Toolbox(http_client, search)


# ---------- 语音模型：第一次用到时加载，加载失败就关掉语音，不影响文字对话 ----------

# /health 用：loading / ready / off / 出错原因
voice_status: dict[str, str] = {"ears": "off", "tts": "off"}
_ears_lock, _voice_lock = threading.Lock(), threading.Lock()


def _load(kind: str, build: Any) -> Any:
    if not settings.voice:
        voice_status[kind] = "off"
        return None
    voice_status[kind] = "loading"
    try:
        t0 = time.perf_counter()
        obj = build()
        voice_status[kind] = "ready"
        log.info("语音模型（%s）加载完成，%.1f 秒", kind, time.perf_counter() - t0)
        return obj
    except Exception as err:  # 缺模型、配置写错：语音不可用，但服务照常启动
        voice_status[kind] = str(err)
        log.warning("语音模型（%s）加载失败：%s", kind, err)
        return None


@lru_cache
def _ears() -> Any:
    from .voice.engines import EarConfig, Ears  # sherpa-onnx 只在用到语音时才导入

    cfg = EarConfig(
        settings.models_dir,
        wake_threshold=settings.wake_threshold,
        end_silence=settings.end_silence,
        language=settings.asr_language,
    )
    return _load("ears", lambda: Ears(cfg))


@lru_cache
def _voice() -> Any:
    from .voice.tts import TtsConfig, Voice

    # 合成越快她开口越早：用一半逻辑核，最多 8 个（实测 4 → 8 线程，短句 330 → 230 毫秒）
    threads = min(8, max(2, (os.cpu_count() or 4) // 2))
    cfg = TtsConfig(settings.models_dir, settings.tts_engine, settings.tts_speaker, settings.tts_speed, threads)
    return _load("tts", lambda: Voice(cfg))


def get_ears() -> Any:
    with _ears_lock:  # 后台预加载和第一个请求可能同时到，只加载一次
        return _ears()


def get_voice() -> Any:
    with _voice_lock:
        return _voice()


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=200)


def build_messages(history: list[ChatMessage], max_history: int) -> list[Message]:
    """系统提示词 + 最近几条对话。连续同一角色的消息合并，有些模型不接受连续两条用户消息。"""
    recent = history[-max_history:]
    while recent and recent[0].role != "user":
        recent = recent[1:]  # 截断后要从用户消息开始

    merged: list[Message] = []
    for m in recent:
        if merged and merged[-1]["role"] == m.role:
            merged[-1]["content"] += "\n" + m.content
        else:
            merged.append({"role": m.role, "content": m.content})
    return [{"role": "system", "content": SYSTEM_PROMPT}, *merged]


def sse(event: Event) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@app.get("/health")
def health(model: ChatModel = Depends(get_model), toolbox: Toolbox = Depends(get_toolbox)) -> dict[str, object]:
    return {
        "ok": True,
        "model": model.name,
        "tools": [t["function"]["name"] for t in toolbox.specs],
        "voice": dict(voice_status),
    }


@app.post("/chat")
async def chat(
    req: ChatRequest,
    model: ChatModel = Depends(get_model),
    toolbox: Toolbox = Depends(get_toolbox),
) -> StreamingResponse:
    messages = build_messages(req.messages, settings.max_history)
    if messages[-1]["role"] != "user":
        raise HTTPException(status_code=422, detail="最后一条必须是用户消息")

    async def events() -> AsyncIterator[str]:
        try:
            async for event in run_agent(model, messages, toolbox):
                yield sse(event)
            yield sse({"type": "done"})
        except Exception as err:  # 出错也要告诉桌面端，让艾拉有反应，而不是一直等
            code, message = classify_error(err)
            log.warning("大模型调用失败（%s）：%s", code, err)
            yield sse({"type": "error", "code": code, "message": message})

    # 客户端断开时 Starlette 会取消这个生成器，大模型请求和工具调用也随之停止
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


class TtsRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    speed: float | None = Field(default=None, ge=0.5, le=2.0)


@app.post("/tts")
async def tts(req: TtsRequest, voice: Any = Depends(get_voice)) -> Response:
    """一句话转语音。没有能念的内容（只有标点、网址）时返回 204"""
    if voice is None:
        raise HTTPException(status_code=503, detail=f"语音合成不可用：{voice_status['tts']}")
    t0 = time.perf_counter()
    samples = await asyncio.to_thread(voice.synthesize, req.text, req.speed)
    if samples is None:
        return Response(status_code=204)
    ms = round((time.perf_counter() - t0) * 1000)
    return Response(float_to_wav(samples, voice.sample_rate), media_type="audio/wav", headers={"X-Synth-Ms": str(ms)})


# 浏览器里任何网页都能连 ws://127.0.0.1，所以只接受本机页面和桌面端（没有 Origin 或 file://）
LOCAL_ORIGIN = re.compile(r"^(https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?|file://|null)$")


@app.websocket("/voice")
async def voice_ws(ws: WebSocket, ears: Any = Depends(get_ears)) -> None:
    """二进制消息：16kHz 单声道 16 位 PCM。文本消息：{"type": "mode", "mode": "off|wake|listen"}"""
    origin = ws.headers.get("origin")
    if origin and not LOCAL_ORIGIN.match(origin):
        log.warning("拒绝了来自 %s 的语音连接", origin)
        await ws.close(code=1008)
        return
    await ws.accept()
    if ears is None:
        await ws.send_json({"type": "error", "message": f"语音识别不可用：{voice_status['ears']}"})
        await ws.close()
        return

    listener = ears.new_listener()
    await ws.send_json({"type": "ready", "mode": listener.mode})
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes"):
                # 识别要一两百毫秒，放到线程里；同一条连接按顺序处理
                for event in await asyncio.to_thread(listener.feed, pcm16_to_float(msg["bytes"])):
                    await ws.send_json(event)
            elif msg.get("text"):
                cmd = _parse_command(msg["text"])
                if cmd.get("type") == "mode" and cmd.get("mode") in ("off", "wake", "listen"):
                    listener.set_mode(cmd["mode"])
                    await ws.send_json({"type": "mode", "mode": listener.mode})
    except WebSocketDisconnect:
        pass


def _parse_command(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}
