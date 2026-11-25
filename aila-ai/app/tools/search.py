"""联网搜索：Tavily（海外，每月 1000 次免费）和博查（国内，中文结果更好），在 .env 里选。

都没配密钥时用 FreeSearch：直接读必应的搜索结果页（国内也打得开），读不到再试 DuckDuckGo。
这样装好就能搜，不用先去申请密钥；代价是没有 API 稳——搜得太勤可能被要求验证、结果页改版也可能认不出来，
这时提示去设置里配博查或 Tavily。结果页用 lxml 解析（trafilatura 本来就依赖它，不算新加依赖）。
"""

import base64
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import parse_qs, urlsplit

import httpx
from lxml import html as lxml_html

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


# ---------- 不用密钥的搜索 ----------

# 像普通浏览器那样要结果页（搜索引擎对不认识的客户端常常只给验证页）
FREE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _text(node: Any) -> str:
    return " ".join(node.text_content().split()) if node is not None else ""


def _bing_target(href: str) -> str:
    """必应有时把结果链接包成 bing.com/ck/a?...&u=a1<base64 网址>，拆出真正的网址"""
    parts = urlsplit(href)
    if parts.hostname and parts.hostname.endswith("bing.com") and parts.path.startswith("/ck/"):
        u = (parse_qs(parts.query).get("u") or [""])[0]
        if u.startswith("a1"):
            raw = u[2:]
            try:
                return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                return href
    return href


def parse_bing(page: str) -> list[SearchResult]:
    """必应结果页：每条是 li.b_algo，标题链接在 h2 里，摘要在 .b_caption 的段落里"""
    if not page.strip():
        return []
    tree = lxml_html.fromstring(page)
    results: list[SearchResult] = []
    for item in tree.xpath('//li[contains(concat(" ", normalize-space(@class), " "), " b_algo ")]'):
        link = next(iter(item.xpath(".//h2//a[@href]")), None)
        if link is None:
            continue
        url = _bing_target(link.get("href", ""))
        if not url.startswith(("http://", "https://")):
            continue
        snippet = next(iter(item.xpath('.//div[contains(@class, "b_caption")]//p')), None)
        if snippet is None:
            snippet = next(iter(item.xpath(".//p")), None)
        results.append(SearchResult(title=_text(link), url=url, snippet=_text(snippet)))
    return results


def _ddg_target(href: str) -> str:
    """DuckDuckGo 的结果链接是 //duckduckgo.com/l/?uddg=<真正的网址>"""
    parts = urlsplit(href if "//" not in href[:2] else "https:" + href)
    if parts.hostname and parts.hostname.endswith("duckduckgo.com") and parts.path.startswith("/l/"):
        return (parse_qs(parts.query).get("uddg") or [href])[0]
    return href


def parse_ddg(page: str) -> list[SearchResult]:
    """DuckDuckGo 的纯网页版：标题是 a.result__a，摘要是 .result__snippet"""
    if not page.strip():
        return []
    tree = lxml_html.fromstring(page)
    results: list[SearchResult] = []
    for item in tree.xpath('//div[contains(concat(" ", normalize-space(@class), " "), " result ")]'):
        link = next(iter(item.xpath('.//a[contains(@class, "result__a")]')), None)
        if link is None:
            continue
        url = _ddg_target(link.get("href", ""))
        if not url.startswith(("http://", "https://")):
            continue
        snippet = next(iter(item.xpath('.//*[contains(@class, "result__snippet")]')), None)
        results.append(SearchResult(title=_text(link), url=url, snippet=_text(snippet)))
    return results


class FreeSearch:
    """不用密钥的搜索：先读必应的结果页，没结果或打不开再试 DuckDuckGo（国内多半打不开，海外用）"""

    name = "free"
    ENGINES = (
        ("必应", "https://www.bing.com/search", parse_bing),
        ("DuckDuckGo", "https://html.duckduckgo.com/html/", parse_ddg),
    )
    HINT = "免费搜索不太稳：没搜到可以换个说法再搜；想更稳更准，在 设置 → 模型 → 联网搜索 填博查或 Tavily 的密钥。"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def search(self, query: str, count: int) -> list[SearchResult]:
        failure: httpx.HTTPError | None = None
        opened = False
        for _name, url, parse in self.ENGINES:
            try:
                resp = await self._client.get(
                    url, params={"q": query}, headers=FREE_HEADERS, timeout=TIMEOUT, follow_redirects=True
                )
                resp.raise_for_status()
                opened = True
                results = parse(resp.text)
            except httpx.HTTPError as err:
                failure = err
                continue
            except ValueError:  # 结果页解析不了（返回的不是网页）
                continue
            if results:
                return results[:count]
        if failure is not None and not opened:
            raise failure  # 一个都没打开：当网络出错报
        return []
