import asyncio

from fetcher import fetch_all


class Client:
    async def get(self, url):
        await asyncio.sleep(0.05 if url == "slow" else 0)
        if url == "bad":
            raise ConnectionError(url)
        return url


def test_order_and_failures():
    result = asyncio.run(fetch_all(Client(), ["slow", "bad", "ok"]))
    assert result[0] == "slow" and result[2] == "ok"
    assert isinstance(result[1], ConnectionError)


def test_empty():
    assert asyncio.run(fetch_all(Client(), [])) == []
