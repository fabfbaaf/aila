"""艾拉的本地 AI 服务：组装各个接口，管理启动和关闭。

- POST /chat：流式回复（文字、表情、动作、进度）
- POST /memory/extract：一轮对话结束后，算出记忆要怎么更新
- GET /skills：技能包列表
- /knowledge：资料库
- POST /tts、WS /voice：语音
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import deps
from .deps import get_confirmations, get_ears, get_embedder, get_knowledge, get_model, get_registry, get_voice
from .routes import chat, knowledge, settings, voice
from .routes.chat import ChatMessage, ChatRequest, build_messages
from .security import LocalOnly

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

__all__ = [
    "ChatMessage", "ChatRequest", "app", "build_messages",
    "get_confirmations", "get_ears", "get_embedder", "get_knowledge", "get_model", "get_registry", "get_voice",
]


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # 语音和向量模型加载要几秒，放到后台，文字对话不用等
    jobs = [asyncio.to_thread(get_embedder)]
    if deps.settings.voice:
        jobs += [asyncio.to_thread(get_ears), asyncio.to_thread(get_voice)]
    preload = asyncio.gather(*jobs)
    mcp = deps.start_mcp()  # 外部 MCP 服务也在后台连接
    yield
    if mcp:
        await mcp.stop()
    if deps.get_browser.cache_info().currsize:  # 打开过浏览器的话关掉
        await deps.get_browser().shutdown()
    await preload
    await deps.http_client.aclose()


app = FastAPI(title="艾拉 AI 服务", version="0.4.0", lifespan=lifespan)
app.add_middleware(LocalOnly)  # 浏览器里别的网页不能调用这些接口
app.include_router(chat.router)
app.include_router(voice.router)
app.include_router(knowledge.router)
app.include_router(settings.router)
