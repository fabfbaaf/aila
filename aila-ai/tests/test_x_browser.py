"""X 浏览器助手通过对话工具箱确认后，只把已核对的目标和草稿交给浏览器。"""

import json
from typing import Any
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import pytest

from app import deps
from app.browser import BrowserError
from app.llm import ToolCall
from app.tools import SkillRegistry, Toolbox
from app.tools.x_browser import x_browser_skill


@pytest.fixture
def browser_box() -> tuple[AsyncMock, Toolbox]:
    browser = AsyncMock()
    return browser, SkillRegistry([x_browser_skill(browser)]).toolbox({"x_browser": True})


def open_call(**args: Any) -> ToolCall:
    return ToolCall("x1", "x_open", json.dumps(args))


def test_registered_and_opt_in() -> None:
    registry = deps.get_registry()
    row = next(row for row in registry.describe() if row["id"] == "x_browser")
    assert row["name"] == "X 浏览器助手" and row["enabled"] is False
    assert row["tools"] == [{"name": "x_open", "risk": "write"}]
    assert "x_open" not in registry.toolbox().names
    schema = next(s["function"]["parameters"] for s in registry.toolbox({"x_browser": True}).specs
                  if s["function"]["name"] == "x_open")
    assert schema["properties"]["page"]["enum"] == ["home", "mentions", "post", "reply"]


@pytest.mark.parametrize(("page", "url"), [("home", "https://x.com/home"),
                                          ("mentions", "https://x.com/notifications/mentions")])
@pytest.mark.anyio
async def test_view_page(browser_box: tuple[AsyncMock, Toolbox], page: str, url: str) -> None:
    browser, box = browser_box
    call = open_call(page=page)
    ask = box.confirmation(call)
    assert ask and ask.detail == url
    assert "需要用户确认" in await box.run(call)
    browser.open_for_user.assert_not_awaited()
    assert "已打开" in await box.run(call, approved=True)
    browser.open_for_user.assert_awaited_once_with(url)


@pytest.mark.parametrize("target", [None, "12345", "https://x.com/i/web/status/12345",
                                    "https://twitter.com/a_b/status/12345?s=20"])
@pytest.mark.anyio
async def test_confirm_full_post_or_reply(browser_box: tuple[AsyncMock, Toolbox], target: str | None) -> None:
    browser, box = browser_box
    text = "工程进展 ✨ & text=二+三 #测试\n" + "还有一点，" * 60
    call = (open_call(page="post", text=text) if target is None
            else open_call(page="reply", text=text, target=target))
    ask = box.confirmation(call)
    assert ask and ask.detail.endswith(text)
    if target:
        assert "https://x.com/i/web/status/12345" in ask.detail
    assert "需要用户确认" in await box.run(call)
    browser.open_for_user.assert_not_awaited()
    assert "亲自点击发布" in await box.run(call, approved=True)
    url = urlsplit(browser.open_for_user.await_args.args[0])
    assert (url.scheme, url.netloc, url.path, url.fragment) == ("https", "x.com", "/intent/tweet", "")
    assert parse_qs(url.query) == ({"text": [text], "in_reply_to": ["12345"]} if target else {"text": [text]})


@pytest.mark.parametrize("args", [
    {"page": ["home"]}, {"page": "delete"}, {"page": "home", "url": "https://evil.example"},
    {"page": "mentions", "text": "草稿"}, {"page": "post", "text": "   "},
    {"page": "post", "text": "a" * 2001}, {"page": "post", "text": "\ud800"},
    {"page": "post", "text": "正文", "target": "12345"}, {"page": "reply", "text": "正文"},
    *({"page": "reply", "text": "你好", "target": target} for target in (
        "https://x.com.evil.example/a/status/1", "https://x.com@evil.example/a/status/1",
        "https://evil.example@x.com/a/status/1", "https://x.com:443/a/status/1",
        "https://x.com/a/status/12%33", "https://x.com/a/status/1/extra", "https://[bad",
        "https://x.com/a/st\tatus/1", "http://x.com/a/status/1", "１２３", 12345,
    )),
])
@pytest.mark.anyio
async def test_bad_input_never_opens(browser_box: tuple[AsyncMock, Toolbox], args: dict[str, Any]) -> None:
    browser, box = browser_box
    call = open_call(**args)
    assert box.confirmation(call) is None
    assert "已打开" not in await box.run(call, approved=True)
    browser.open_for_user.assert_not_awaited()


@pytest.mark.anyio
async def test_browser_error_reaches_chat(browser_box: tuple[AsyncMock, Toolbox]) -> None:
    browser, box = browser_box
    browser.open_for_user.side_effect = BrowserError("打不开浏览器。")
    assert await box.run(open_call(page="home"), approved=True) == "打不开浏览器。"
