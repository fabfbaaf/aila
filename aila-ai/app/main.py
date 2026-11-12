"""HTTP 接口：桌面端发来对话历史，这里流式返回文字、表情和动作事件（SSE）"""

import json
import logging
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .config import load_settings
from .llm import ChatModel, MockModel, OpenAICompatibleModel, classify_error
from .persona import SYSTEM_PROMPT
from .tags import Event, TagParser

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("aila")

settings = load_settings()
app = FastAPI(title="艾拉 AI 服务", version="0.1.0")


@lru_cache
def get_model() -> ChatModel:
    if settings.mock:
        log.warning("没有配置 LLM_API_KEY，使用假模型（只会几句固定回复）")
        return MockModel()
    log.info("大模型：%s（%s）", settings.llm_model, settings.llm_base_url)
    return OpenAICompatibleModel(settings)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=200)


def build_messages(history: list[ChatMessage], max_history: int) -> list[dict[str, str]]:
    """系统提示词 + 最近几条对话。连续同一角色的消息合并，有些模型不接受连续两条用户消息。"""
    recent = history[-max_history:]
    while recent and recent[0].role != "user":
        recent = recent[1:]  # 截断后要从用户消息开始

    merged: list[dict[str, str]] = []
    for m in recent:
        if merged and merged[-1]["role"] == m.role:
            merged[-1]["content"] += "\n" + m.content
        else:
            merged.append({"role": m.role, "content": m.content})
    return [{"role": "system", "content": SYSTEM_PROMPT}, *merged]


def sse(event: Event) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@app.get("/health")
def health(model: ChatModel = Depends(get_model)) -> dict[str, object]:
    return {"ok": True, "model": model.name}


@app.post("/chat")
async def chat(req: ChatRequest, model: ChatModel = Depends(get_model)) -> StreamingResponse:
    messages = build_messages(req.messages, settings.max_history)
    if messages[-1]["role"] != "user":
        raise HTTPException(status_code=422, detail="最后一条必须是用户消息")

    async def events() -> AsyncIterator[str]:
        parser = TagParser()
        try:
            async for chunk in model.stream(messages):
                for event in parser.feed(chunk):
                    yield sse(event)
            for event in parser.flush():
                yield sse(event)
            yield sse({"type": "done"})
        except Exception as err:  # 出错也要告诉桌面端，让艾拉有反应，而不是一直等
            code, message = classify_error(err)
            log.warning("大模型调用失败（%s）：%s", code, err)
            yield sse({"type": "error", "code": code, "message": message})

    # 客户端断开时 Starlette 会取消这个生成器，大模型请求也随之停止
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
