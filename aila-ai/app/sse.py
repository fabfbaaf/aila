"""SSE 心跳：事件流里隔一会儿没有事件，就发一行注释（`: ping`）。

为什么要它：桌面端（Electron 主进程）用 Node 自带的 fetch 收事件流，默认 5 分钟收不到任何数据就把连接断掉；
她跑一条很久的命令、模型一步想很久的时候，事件流会安静好几分钟，任务页就显示「连不上 AI 服务」。
注释行按 SSE 的规定会被客户端忽略（桌面端的 SseParser 只看 data: 行），只是让连接一直有数据走。
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator

HEARTBEAT_SECONDS = 15.0
PING = ": ping\n\n"


async def with_heartbeat(chunks: AsyncIterator[str], interval: float = HEARTBEAT_SECONDS) -> AsyncIterator[str]:
    """把一串已经拼好的 SSE 文本原样往外送；中间超过 interval 秒没有新的就插一行心跳"""
    it = chunks.__aiter__()
    pending: asyncio.Future[str] | None = asyncio.ensure_future(it.__anext__())
    try:
        while True:
            done, _ = await asyncio.wait({pending}, timeout=interval)
            if not done:
                yield PING
                continue
            try:
                chunk = pending.result()
            except StopAsyncIteration:
                pending = None
                return
            yield chunk
            pending = asyncio.ensure_future(it.__anext__())
    finally:
        # 客户端断开、出错：停掉还在等的那一步，再把底下的生成器关掉
        if pending is not None and not pending.done():
            pending.cancel()
            with contextlib.suppress(BaseException):
                await pending
        aclose = getattr(it, "aclose", None)
        if aclose is not None:
            with contextlib.suppress(BaseException):
                await aclose()
