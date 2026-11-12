"""搜索服务的请求格式和返回解析，以及搜索结果交给大模型时的样子"""

import json
from typing import Any

import httpx
import pytest

from app.llm import ToolCall
from app.tools import SNIPPET_CHARS, Toolbox
from app.tools.search import BochaSearch, SearchResult, TavilySearch


def recording_client(reply: Any, seen: list[httpx.Request], status: int = 200) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=reply)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.anyio
async def test_tavily_request_and_parsing() -> None:
    seen: list[httpx.Request] = []
    reply = {"results": [{"title": "Python 3.13", "url": "https://docs.python.org/3.13/", "content": "新特性……"}]}
    results = await TavilySearch("tv-key", recording_client(reply, seen)).search("Python 3.13", 5)

    assert results == [SearchResult("Python 3.13", "https://docs.python.org/3.13/", "新特性……")]
    req = seen[0]
    assert str(req.url) == TavilySearch.URL
    assert req.headers["authorization"] == "Bearer tv-key"
    assert json.loads(req.content) == {"query": "Python 3.13", "max_results": 5, "search_depth": "basic"}


@pytest.mark.anyio
@pytest.mark.parametrize("wrapped", [True, False])
async def test_bocha_request_and_parsing(wrapped: bool) -> None:
    pages = {
        "webPages": {
            "value": [
                {"name": "有摘要", "url": "https://a.example/", "summary": "长摘要", "snippet": "短摘要"},
                {"name": "只有片段", "url": "https://b.example/", "snippet": "短摘要"},
            ]
        }
    }
    reply = {"code": 200, "data": pages} if wrapped else pages
    seen: list[httpx.Request] = []
    results = await BochaSearch("bc-key", recording_client(reply, seen)).search("艾拉", 3)

    assert results == [
        SearchResult("有摘要", "https://a.example/", "长摘要"),
        SearchResult("只有片段", "https://b.example/", "短摘要"),
    ]
    req = seen[0]
    assert str(req.url) == BochaSearch.URL
    assert req.headers["authorization"] == "Bearer bc-key"
    assert json.loads(req.content) == {"query": "艾拉", "count": 3, "summary": True}


@pytest.mark.anyio
async def test_bocha_without_results() -> None:
    results = await BochaSearch("k", recording_client({"code": 200, "data": {"webPages": None}}, [])).search("x", 3)
    assert results == []


@pytest.mark.anyio
async def test_search_results_given_to_model() -> None:
    reply = {"results": [{"title": "标题", "url": "https://a.example/", "content": "字" * 1000}]}
    client = recording_client(reply, [])
    toolbox = Toolbox(client, TavilySearch("k", client))
    text = await toolbox.run(ToolCall("c", "web_search", '{"query": "测试"}'))

    assert text.startswith("搜索「测试」的结果。")
    assert "1. 标题\n   https://a.example/\n   " in text
    assert "字" * SNIPPET_CHARS in text and "字" * (SNIPPET_CHARS + 1) not in text


@pytest.mark.anyio
async def test_bad_search_key_is_reported() -> None:
    client = recording_client({"detail": "invalid key"}, [], status=401)
    toolbox = Toolbox(client, TavilySearch("wrong", client))
    assert await toolbox.run(ToolCall("c", "web_search", '{"query": "x"}')) == "请求失败（HTTP 401）"
