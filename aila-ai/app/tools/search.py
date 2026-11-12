"""联网搜索：Tavily（海外，每月 1000 次免费）和博查（国内，中文结果更好），在 .env 里选"""

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

TIMEOUT = 15.0


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


class SearchProvider(Protocol):
    name: str

    async def search(self, query: str, count: int) -> list[SearchResult]: ...


class TavilySearch:
    name = "tavily"
    URL = "https://api.tavily.com/search"

    def __init__(self, api_key: str, client: httpx.AsyncClient) -> None:
        self._key = api_key
        self._client = client

    async def search(self, query: str, count: int) -> list[SearchResult]:
        resp = await self._client.post(
            self.URL,
            headers={"Authorization": f"Bearer {self._key}"},
            json={"query": query, "max_results": count, "search_depth": "basic"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return [
            SearchResult(title=r.get("title", ""), url=r.get("url", ""), snippet=r.get("content", ""))
            for r in resp.json().get("results", [])
        ]


class BochaSearch:
    name = "bocha"
    URL = "https://api.bochaai.com/v1/web-search"

    def __init__(self, api_key: str, client: httpx.AsyncClient) -> None:
        self._key = api_key
        self._client = client

    async def search(self, query: str, count: int) -> list[SearchResult]:
        resp = await self._client.post(
            self.URL,
            headers={"Authorization": f"Bearer {self._key}"},
            json={"query": query, "count": count, "summary": True},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        body: dict[str, Any] = resp.json()
        # 有的返回把结果包在 data 里，有的直接在顶层，两种都认
        payload = body["data"] if isinstance(body.get("data"), dict) else body
        items = (payload.get("webPages") or {}).get("value") or []
        return [
            SearchResult(
                title=i.get("name", ""),
                url=i.get("url", ""),
                snippet=i.get("summary") or i.get("snippet", ""),
            )
            for i in items
        ]


class MockSearch:
    """假模型配套的假搜索：不联网，结果明确标成假数据"""

    name = "mock"

    async def search(self, query: str, count: int) -> list[SearchResult]:
        return [
            SearchResult(
                title=f"（假数据）关于「{query}」的第 {i} 条结果",
                url=f"https://example.com/mock/{i}",
                snippet="这是假搜索返回的示例内容。配置 SEARCH_API_KEY 后会换成真实搜索结果。",
            )
            for i in range(1, min(count, 3) + 1)
        ]
