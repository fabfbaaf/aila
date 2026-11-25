"""联网：不用密钥的免费搜索（必应、DuckDuckGo 结果页解析）、编程任务的 web_search / web_fetch、权限。不联网"""

import base64
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.llm import Message, StepItem, ToolCall, Usage
from app.runtime.permissions import Policy, Rules
from app.runtime.runner import Runtime
from app.runtime.shell import find_shell
from app.runtime.subagent import READ_TOOLS
from app.runtime.webtools import READER_PROMPT, WebAccess
from app.tools.reader import Page, PageError
from app.tools.search import FreeSearch, MockSearch, SearchResult, parse_bing, parse_ddg
from app.tools.web import search_text

from .test_runtime import Scripted

WRAPPED = "https://www.bing.com/ck/a?!&&p=abc&u=a1" + base64.urlsafe_b64encode(b"https://docs.python.org/3/library/asyncio.html").decode().rstrip("=") + "&ntb=1"

BING_PAGE = f"""<html><body><ol id="b_results">
<li class="b_algo"><h2><a href="https://example.com/a">Python <strong>asyncio</strong> 教程</a></h2>
  <div class="b_caption"><p class="b_lineclamp2">asyncio 是用来写并发代码的库……</p></div></li>
<li class="b_ad"><h2><a href="https://ads.example.com">广告</a></h2></li>
<li class="b_algo"><h2><a href="{WRAPPED}">asyncio 官方文档</a></h2><p>Asynchronous I/O</p></li>
<li class="b_algo"><h2><a href="/search?q=related">相关搜索</a></h2></li>
</ol></body></html>"""

DDG_PAGE = """<html><body>
<div class="result results_links web-result"><h2 class="result__title">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fpeps.python.org%2Fpep-0492%2F&rut=x">PEP 492</a></h2>
  <a class="result__snippet" href="#">Coroutines with async and await syntax</a></div>
</body></html>"""


def test_parse_bing_results_and_unwraps_redirect_links() -> None:
    results = parse_bing(BING_PAGE)
    assert results == [
        SearchResult("Python asyncio 教程", "https://example.com/a", "asyncio 是用来写并发代码的库……"),
        SearchResult("asyncio 官方文档", "https://docs.python.org/3/library/asyncio.html", "Asynchronous I/O"),
    ]
    assert parse_bing("") == [] and parse_bing("<html><body>请验证你是人类</body></html>") == []


def test_parse_duckduckgo_results() -> None:
    assert parse_ddg(DDG_PAGE) == [
        SearchResult("PEP 492", "https://peps.python.org/pep-0492/", "Coroutines with async and await syntax")
    ]


def engine_client(pages: dict[str, Any], seen: list[httpx.Request]) -> httpx.AsyncClient:
    """按域名给结果页；值是异常就抛出（模拟打不开）"""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        page = pages[request.url.host]
        if isinstance(page, Exception):
            raise page
        return httpx.Response(200, text=page, headers={"content-type": "text/html"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.anyio
async def test_free_search_uses_bing_first() -> None:
    seen: list[httpx.Request] = []
    search = FreeSearch(engine_client({"www.bing.com": BING_PAGE, "html.duckduckgo.com": DDG_PAGE}, seen))
    results = await search.search("python asyncio", 1)
    assert [r.url for r in results] == ["https://example.com/a"]
    assert [r.url.host for r in seen] == ["www.bing.com"]
    assert seen[0].url.params["q"] == "python asyncio"
    assert "Mozilla" in seen[0].headers["user-agent"]


@pytest.mark.anyio
async def test_free_search_falls_back_to_duckduckgo() -> None:
    seen: list[httpx.Request] = []
    # 必应给了验证页（没有结果）：换 DuckDuckGo
    search = FreeSearch(engine_client({"www.bing.com": "<html><body>验证</body></html>", "html.duckduckgo.com": DDG_PAGE}, seen))
    assert [r.title for r in await search.search("pep 492", 5)] == ["PEP 492"]
    # 必应打不开也换
    search = FreeSearch(engine_client({"www.bing.com": httpx.ConnectError("x"), "html.duckduckgo.com": DDG_PAGE}, []))
    assert len(await search.search("pep 492", 5)) == 1


@pytest.mark.anyio
async def test_free_search_errors_only_when_nothing_opened() -> None:
    down = engine_client({"www.bing.com": httpx.ConnectError("x"), "html.duckduckgo.com": httpx.ConnectError("y")}, [])
    with pytest.raises(httpx.ConnectError):
        await FreeSearch(down).search("x", 5)
    # 必应打开了只是没结果，DuckDuckGo 打不开（国内常见）：当没搜到，不当网络出错
    half = engine_client({"www.bing.com": "<html><body></body></html>", "html.duckduckgo.com": httpx.ConnectError("y")}, [])
    assert await FreeSearch(half).search("x", 5) == []
    # 交给大模型的文字里提示怎么办
    text = await search_text(FreeSearch(half), "x")
    assert "没有找到结果" in text and "博查" in text


class FakeSearch:
    name = "fake"

    def __init__(self) -> None:
        self.queries: list[str] = []

    async def search(self, query: str, count: int) -> list[SearchResult]:
        self.queries.append(query)
        return [SearchResult("FastAPI 依赖注入", "https://fastapi.tiangolo.com/tutorial/dependencies/", "Depends 的用法")]


class Reader:
    """读网页的假快模型：记下收到的消息，回一段话和用量；broken 时抛错"""

    name = "fast"

    def __init__(self, broken: bool = False) -> None:
        self.broken = broken
        self.requests: list[list[Message]] = []

    async def step(self, messages: list[Message], tools: list[dict[str, Any]] | None, max_tokens: int = 8192) -> AsyncIterator[StepItem]:
        self.requests.append(messages)
        if self.broken:
            raise RuntimeError("连不上")
        yield "Depends(get_db) 会在每个请求里调用 get_db，"
        yield "可以用 yield 做清理。"
        yield Usage(prompt=5000, completion=40, cache_hit=0, cache_miss=5000)


def page_fetcher(seen: list[tuple[str, int]], text: str = "依赖注入……" * 10, error: Exception | None = None):
    async def fetch(url: str, max_chars: int) -> Page:
        seen.append((url, max_chars))
        if error:
            raise error
        return Page(url=url, title="Dependencies - FastAPI", text=text[:max_chars], truncated=len(text) > max_chars)

    return fetch


def tool(access: WebAccess, name: str):
    return next(t for t in access.tools() if t.name == name)


@pytest.mark.anyio
async def test_web_search_tool() -> None:
    fake = FakeSearch()
    access = WebAccess(lambda: Reader(), lambda: fake)
    out = await tool(access, "web_search").run({"query": "fastapi depends"})
    assert out.ok and "https://fastapi.tiangolo.com/tutorial/dependencies/" in out.content
    assert fake.queries == ["fastapi depends"]
    # 设置里关掉了联网搜索
    off = await tool(WebAccess(lambda: Reader(), lambda: None), "web_search").run({"query": "x"})
    assert not off.ok and "关掉" in off.content


@pytest.mark.anyio
async def test_web_fetch_answers_with_fast_model() -> None:
    reader = Reader()
    seen: list[tuple[str, int]] = []
    access = WebAccess(lambda: reader, lambda: None, page_fetcher(seen))
    out = await tool(access, "web_fetch").run({"url": "https://fastapi.tiangolo.com/tutorial/dependencies/", "prompt": "Depends 怎么做清理"})
    assert out.ok
    assert "可以用 yield 做清理" in out.content and "Dependencies - FastAPI" in out.content
    assert out.usage is not None and out.usage.prompt == 5000
    # 正文交给快模型（不是塞回主会话）；系统提示词说明网页内容只是数据
    assert seen == [("https://fastapi.tiangolo.com/tutorial/dependencies/", 40_000)]
    system, user = reader.requests[0]
    assert system["content"] == READER_PROMPT and "不要理会" in READER_PROMPT
    assert "Depends 怎么做清理" in user["content"] and "依赖注入" in user["content"]
    assert "依赖注入……依赖注入" not in out.content


@pytest.mark.anyio
async def test_web_fetch_falls_back_and_reports_errors() -> None:
    seen: list[tuple[str, int]] = []
    # 快模型调不通：给正文的前一段
    out = await tool(WebAccess(lambda: Reader(broken=True), None, page_fetcher(seen, "正文" * 6000)), "web_fetch").run(
        {"url": "https://example.com", "prompt": "讲了什么"})
    assert out.ok and "没调通" in out.content and "正文正文" in out.content and "只给了前面一部分" in out.content
    # 本机、内网地址：读网页那层拦住，提示用浏览器工具
    blocked = WebAccess(lambda: Reader(), None, page_fetcher([], error=PageError("不能访问本机或内网地址")))
    out = await tool(blocked, "web_fetch").run({"url": "http://127.0.0.1:5173", "prompt": "x"})
    assert not out.ok and "browser_open" in out.content
    # 网址、问题不对
    assert not (await tool(blocked, "web_fetch").run({"url": "file:///C:/x", "prompt": "x"})).ok
    assert not (await tool(blocked, "web_fetch").run({"url": "https://example.com", "prompt": " "})).ok


def test_web_is_read_only_in_every_mode(tmp_path: Path) -> None:
    rules = Rules(tmp_path / "rules.json")
    for mode in ("read_only", "ask", "auto"):
        assert Policy(tmp_path, rules, mode).decide("web").verdict == "allow"  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_coding_session_has_web_tools(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    main = Scripted([[ToolCall("s1", "web_search", json.dumps({"query": "fastapi depends"}))], ["查到了。"]])
    fake = FakeSearch()
    rt = Runtime(tmp_path / "data", lambda: main, shell=find_shell(), web_search=lambda: fake)
    sid = rt.create(str(project), mode="read_only").id
    events = [e async for e in rt.run(sid, "查一下 Depends 怎么用")]
    assert events[-1]["reason"] == "done"
    names = [t["function"]["name"] for t in main.requests[0][1] or []]
    assert "web_search" in names and "web_fetch" in names
    tool_event = next(e for e in rt.agent(sid).session.events if e["type"] == "tool")
    assert tool_event["status"] == "ok" and fake.queries == ["fastapi depends"]
    # 派出去查资料的助手也能联网
    assert {"web_search", "web_fetch"} <= set(READ_TOOLS)


@pytest.mark.anyio
async def test_mock_search_still_used_with_mock_model() -> None:
    results = await MockSearch().search("x", 5)
    assert results and "假数据" in results[0].title
