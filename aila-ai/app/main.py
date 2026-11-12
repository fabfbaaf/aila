"""HTTP 接口：桌面端发来对话历史，这里流式返回文字、表情、动作和进度事件（SSE）"""

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .agent import run_agent
from .config import load_settings
from .llm import ChatModel, Message, MockModel, OpenAICompatibleModel, classify_error
from .persona import SYSTEM_PROMPT
from .tags import Event
from .tools import Toolbox
from .tools.search import BochaSearch, MockSearch, SearchProvider, TavilySearch

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("aila")

settings = load_settings()
# 搜索和读网页共用一个连接池
http_client = httpx.AsyncClient()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await http_client.aclose()


app = FastAPI(title="艾拉 AI 服务", version="0.2.0", lifespan=lifespan)


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
    return {"ok": True, "model": model.name, "tools": [t["function"]["name"] for t in toolbox.specs]}


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
