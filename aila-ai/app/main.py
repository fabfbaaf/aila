"""艾拉的本地 AI 服务：组装各个接口，管理启动和关闭。

- POST /chat：流式回复（文字、表情、动作、进度）
- POST /memory/extract：一轮对话结束后，算出记忆要怎么更新
- GET /skills：技能包列表
- /knowledge：资料库
- POST /tts、WS /voice：语音
- /agent：编程 Agent（会话、一轮任务的事件流、打断、权限模式、确认）
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.exception_handlers import http_exception_handler

from .i18n import localize

from . import deps
from .deps import get_confirmations, get_ears, get_embedder, get_knowledge, get_model, get_registry, get_voice
from .routes import agenda, agent, android, chat, schedules, code, computer, discoveries, extensions, knowledge, phone, quotas, screen, settings, studio, taskwork, voice
from .routes.chat import ChatMessage, ChatRequest, build_messages
from .runtime.taskwork import STATE_FILE
from .security import LocalOnly

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

__all__ = [
    "ChatMessage", "ChatRequest", "app", "build_messages",
    "get_confirmations", "get_ears", "get_embedder", "get_knowledge", "get_model", "get_registry", "get_voice",
]


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # 向量模型（记忆、资料库）加载要几秒，放到后台，文字对话不用等。
    # 语音的两个模型占好几百 MB，不在这里加载：桌面端开着「说话」「唤醒」时调 /voice/warmup，或者第一次用到时再加载
    preload = asyncio.gather(asyncio.to_thread(get_embedder))
    deps.start_mcp()  # 外部 MCP 服务也在后台连接（设置页改了配置会重连，见 deps.restart_mcp）
    await deps.apply_phone()  # 打开过「允许手机连接」的话，局域网上的门跟着开
    deps.get_scheduler().start()  # 定时任务：每 30 秒看一眼，到点的跑
    deps.get_meetings().start()  # 会前 10 分钟提醒到手机
    if (deps.settings.data_dir / STATE_FILE).is_file():
        deps.get_runtime().taskwork.start()
    yield
    await deps.get_meetings().stop()
    await deps.get_scheduler().stop()
    await deps.get_phone_server().stop()
    await deps.stop_mcp()
    await deps.stop_catalog()
    if deps.get_runtime.cache_info().currsize:  # 正在做的编程任务：打断（日志会补完整，下次可以接着做）
        await deps.get_runtime().shutdown()
    if deps.get_browser.cache_info().currsize:  # 打开过浏览器的话关掉
        await deps.get_browser().shutdown()
    await preload
    await deps.http_client.aclose()


app = FastAPI(title="艾拉 AI 服务", version="0.3.2", lifespan=lifespan)  # 和桌面端 package.json 的版本一致
app.add_middleware(LocalOnly)  # 浏览器里别的网页不能调用这些接口


@app.exception_handler(HTTPException)
async def localized_http_error(request, exc: HTTPException):  # noqa: ANN001, ANN201
    """错误说明发给桌面端之前，英文界面时换成英文（app/i18n.py）；别的和 FastAPI 默认的一样"""
    detail = localize(exc.detail) if isinstance(exc.detail, str) else exc.detail
    return await http_exception_handler(request, HTTPException(exc.status_code, detail, exc.headers))
app.include_router(chat.router)
app.include_router(voice.router)
app.include_router(knowledge.router)
app.include_router(settings.router)
app.include_router(screen.router)
app.include_router(code.router)
app.include_router(agent.router)
app.include_router(taskwork.router)
app.include_router(quotas.router)
app.include_router(computer.router)
app.include_router(studio.router)
app.include_router(discoveries.router)
app.include_router(extensions.router)
app.include_router(phone.router)
app.include_router(android.router)
app.include_router(schedules.router)
app.include_router(agenda.router)
