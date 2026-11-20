"""SSE 心跳：安静太久插一行注释，事件原样照送，客户端断开时底下的生成器被关掉"""

import asyncio

import pytest

from app.sse import PING, with_heartbeat


@pytest.mark.anyio
async def test_quiet_stream_gets_pings_and_events_pass_through() -> None:
    async def slow():
        yield "data: 1\n\n"
        await asyncio.sleep(0.35)
        yield "data: 2\n\n"

    got = [chunk async for chunk in with_heartbeat(slow(), interval=0.1)]
    assert got[0] == "data: 1\n\n" and got[-1] == "data: 2\n\n"
    assert got.count(PING) >= 2  # 中间安静了 0.35 秒：每 0.1 秒一行心跳
    assert all(c == PING for c in got[1:-1])


@pytest.mark.anyio
async def test_busy_stream_has_no_pings() -> None:
    async def fast():
        for i in range(5):
            yield f"data: {i}\n\n"

    assert [c async for c in with_heartbeat(fast(), interval=0.5)] == [f"data: {i}\n\n" for i in range(5)]


@pytest.mark.anyio
async def test_client_disconnect_closes_the_source() -> None:
    closed = asyncio.Event()

    async def endless():
        try:
            while True:
                await asyncio.sleep(10)
                yield "data: never\n\n"
        finally:
            closed.set()

    stream = with_heartbeat(endless(), interval=0.05)
    assert await stream.__anext__() == PING
    await stream.aclose()  # 客户端走了
    await asyncio.wait_for(closed.wait(), 1)
