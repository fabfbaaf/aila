import asyncio
import time

from fetcher import fetch_all


class SlowClient:
    async def get(self, url):
        await asyncio.sleep(0.2)
        return url.upper()


def test_runs_concurrently():
    started = time.perf_counter()
    result = asyncio.run(fetch_all(SlowClient(), ["a", "b", "c", "d", "e"]))
    assert result == ["A", "B", "C", "D", "E"]
    assert time.perf_counter() - started < 0.6
