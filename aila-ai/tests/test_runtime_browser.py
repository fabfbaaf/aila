"""编程浏览器验收。用假标签页执行工具，地址检查走真实规则，不打开 Edge。"""

import asyncio
import re
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.browser.session import BrowserError, BrowserSession, CodingBrowserPool
from app.browser.snapshot import Element
from app.runtime import browsertools
from app.runtime.mcp import McpTool
from app.runtime.runner import Runtime
from app.runtime.session import Event

from .test_runtime import SHELL, Scripted, call

LOCAL = "http://127.0.0.1:5173"
PUBLIC = "https://example.com"
PNG_DATA = b"\x89PNG\r\n\x1a\n" + b"fake"
BROWSER_TOOLS = [
    "browser_open", "browser_read", "browser_click", "browser_type", "browser_select", "browser_scroll",
    "browser_back", "browser_press", "browser_tabs", "browser_upload", "browser_screenshot",
]


async def public_dns(_: str) -> list[str]:
    return ["93.184.216.34"]


class FakeBrowser:
    def __init__(self, pool: CodingBrowserPool) -> None:
        address_guard = pool.session()
        self.check_port = address_guard.check_port
        self.allow_port = address_guard.allow_port
        self.port_allowed = address_guard.port_allowed
        self.check_url = address_guard.check_url
        self.page_info = ("", "")
        self.calls: list[tuple] = []
        self.closed = False
        self.pages: dict[int, str] = {}
        self.active = 1
        self.focused_secret = False
        self.elements = {
            1: Element(1, "button", "保存", "button", submits=True, method="post", action=PUBLIC + "/save"),
            2: Element(2, "input", "题目", "input"),
            3: Element(3, "input", "密码", "input", type="password", sensitive=True),
            4: Element(4, "file", "附件", "input", type="file"),
            5: Element(5, "link", "主页", "a", href=PUBLIC + "/home"),
            6: Element(6, "button", "搜索", "button", submits=True, method="get", action=PUBLIC + "/search"),
            7: Element(7, "input", "搜索词", "input", method="get", action=PUBLIC + "/search"),
            8: Element(8, "select", "语言", "select"),
        }

    def element(self, ref: int) -> Element | None:
        return self.elements.get(ref)

    async def open(self, url: str) -> str:
        await self.check_url(url)
        self.pages[self.active] = url
        self.page_info = ("测试页面", url)
        self.calls.append(("open", url))
        return await self.read()

    async def read(self) -> str:
        return "测试页面：" + self.page_info[1]

    async def click(self, ref: int, approved: bool) -> str:
        self.calls.append(("click", ref, approved))
        return await self.read()

    async def type(self, ref: int, text: str, submit: bool, approved: bool) -> str:
        self.calls.append(("type", ref, text, submit, approved))
        return await self.read()

    async def select(self, ref: int, option: str) -> str:
        self.calls.append(("select", ref, option))
        return await self.read()

    async def press(self, key: str, *, expected_url: str) -> str:
        assert expected_url == self.page_info[1]
        if self.focused_secret:
            raise BrowserError(browsertools.SENSITIVE)
        self.calls.append(("press", key))
        return await self.read()

    async def upload(self, ref: int, path: Path) -> str:
        self.calls.append(("upload", ref, path))
        return await self.read()

    async def tabs(self, action: str = "list", tab: int | None = None) -> str:
        if action == "list":
            return "\n".join(f"[{idx}] {url}" for idx, url in self.pages.items())
        if tab not in self.pages:
            raise BrowserError("没有这个标签页。")
        assert tab is not None
        if action == "switch":
            self.active = tab
            self.page_info = ("测试页面", self.pages[tab])
        else:
            del self.pages[tab]
        return await self.read()

    async def scroll(self, down: bool) -> str:
        return "向下翻页" if down else "向上翻页"

    async def back(self) -> str:
        return "返回"

    async def screenshot(self, ref: int | None = None) -> tuple[bytes, str]:
        self.calls.append(("screenshot", ref))
        return PNG_DATA, "标题：测试页面\n[1] 保存"

    async def close(self) -> None:
        self.pages.clear()
        self.closed = True


class FakePool:
    def __init__(self) -> None:
        self.guards = CodingBrowserPool(public_dns, forbidden_ports={8765, 8790, 9456})
        self.sessions: list[FakeBrowser] = []

    def session(self) -> FakeBrowser:
        browser = FakeBrowser(self.guards)
        self.sessions.append(browser)
        return browser


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Runtime, FakePool]:
    pool = FakePool()
    monkeypatch.setattr(browsertools, "browser_pool", lambda: pool)
    return Runtime(tmp_path / "data", lambda: Scripted([["好了。"]]), shell=SHELL), pool


async def invoke(rt: Runtime, sid: str, name: str, *,
                 approve: bool = True, remember: bool = False,
                 during_confirm: Callable[[], None] | None = None, **args: Any) -> list[Event]:
    out = []
    async for event in rt.agent(sid)._run_tool(call(f"browser_{name}", **args)):
        out.append(event)
        if event["type"] == "confirm":
            if during_confirm is not None:
                during_confirm()
            assert rt.confirms.answer(event["id"], approve, remember)
    return out


def confirms(events: list[Event]) -> list[Event]:
    return [event for event in events if event["type"] == "confirm"]


@pytest.mark.anyio
async def test_browser_order_tabs_and_delete(harness: tuple[Runtime, FakePool], tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    rt, pool = harness
    mcp = McpTool("demo", "test", "测试工具", {})
    monkeypatch.setattr(rt.mcp, "tools", lambda: [mcp])
    first = rt.create(str(tmp_path), "auto")
    second = rt.create(str(tmp_path), "auto")
    for session in (first, second):
        names = list(rt.agent(session.id).tools.tools)
        assert names[names.index("remember") - len(BROWSER_TOOLS):] == [*BROWSER_TOOLS, "remember", mcp.full_name]
    assert (await invoke(rt, first.id, "open", url=LOCAL))[-1]["status"] == "ok"
    assert (await invoke(rt, second.id, "open", url=PUBLIC))[-1]["status"] == "ok"
    own, other = pool.sessions
    own.pages[2] = LOCAL + "/extra"
    assert LOCAL in (await invoke(rt, first.id, "tabs"))[-1]["content"]
    assert LOCAL not in (await invoke(rt, second.id, "tabs"))[-1]["content"]
    assert (await invoke(rt, second.id, "tabs", action="switch", tab=2))[-1]["status"] == "error"
    assert (await invoke(rt, first.id, "tabs", action="switch", tab=2))[-1]["status"] == "ok"
    assert own.page_info[1].endswith("/extra") and other.page_info[1] == PUBLIC

    result = (await invoke(rt, first.id, "screenshot", ref=1))[-1]
    assert result["status"] == "ok" and re.fullmatch(r"[0-9a-f]{32}\.png", result["screenshot"])
    folder = first.path.with_suffix(".shots")
    assert (folder / result["screenshot"]).read_bytes() == PNG_DATA
    assert "测试页面" in result["content"] and result["screenshot"] in result["content"]
    await rt.delete(first.id)
    assert not folder.exists() and own.closed and not other.closed
    assert len(other.pages) == 1
    await rt.shutdown()
    assert other.closed


@pytest.mark.anyio
async def test_local_port_approval_scoped_by_project_and_denies_private_network(
    harness: tuple[Runtime, FakePool], tmp_path: Path,
) -> None:
    rt, pool = harness
    project = tmp_path / "project"
    project.mkdir()
    session = rt.create(str(project), "auto")
    denied = await invoke(rt, session.id, "open", url=LOCAL, approve=False, remember=True)
    assert denied[-1]["status"] == "rejected" and not pool.sessions[0].calls
    assert not pool.sessions[0].port_allowed(5173)
    granted = await invoke(rt, session.id, "open", url=LOCAL, remember=True)
    confirmation, = confirms(granted)
    assert confirmation["kind"] == "browser_local" and confirmation["can_remember"]
    assert "5173" in confirmation["reason"] and LOCAL in confirmation["detail"]
    assert not confirms(await invoke(rt, session.id, "open", url=LOCAL + "/again"))
    assert not rt.rules.commands(project)
    other_session = rt.create(str(project), "read_only")
    approved = await invoke(rt, other_session.id, "open", url="http://[::1]:5173")
    assert approved[-1]["status"] == "ok" and not confirms(approved)
    assert confirms(await invoke(rt, other_session.id, "open", url="http://localhost:5174"))
    other_project = rt.create(str(tmp_path), "auto")
    assert confirms(await invoke(rt, other_project.id, "open", url="http://localhost:5173"))
    for url in ("http://localhost:8765", "http://127.0.0.1:8790", "http://[::1]:9456",
                "http://192.168.1.1", "http://10.0.0.1"):
        result = await invoke(rt, session.id, "open", url=url)
        assert result[-1]["status"] == "error" and not confirms(result), url
    await rt.shutdown()


@pytest.mark.anyio
async def test_same_request_guard_blocks_development_page_fetch_to_internal_service() -> None:
    pool = CodingBrowserPool(public_dns, forbidden_ports={8765, 8790})
    coding = pool.session()
    coding.allow_port(5173)
    decisions: list[str] = []

    async def abort(_: str) -> None:
        decisions.append("abort")

    async def continue_() -> None:
        decisions.append("continue")

    route = SimpleNamespace(abort=abort, continue_=continue_)
    for url in (LOCAL + "/script.js", "http://127.0.0.1:8765/agent/sessions", "http://[::1]:8790/api",
                "http://localhost:5199/internal", PUBLIC + "/cdn.js"):
        await coding._guard(route, SimpleNamespace(url=url, resource_type="script"))
    assert decisions == ["continue", "abort", "abort", "abort", "continue"]
    chat = BrowserSession(Path("unused"), public_dns)
    with pytest.raises(BrowserError):
        await chat.open(LOCAL)


@pytest.mark.anyio
@pytest.mark.parametrize(("mode", "url", "must_ask", "status"), [
    ("read_only", LOCAL, False, "denied"), ("ask", LOCAL, True, "ok"), ("auto", LOCAL, False, "ok"),
    ("read_only", PUBLIC, False, "denied"), ("ask", PUBLIC, True, "ok"), ("auto", PUBLIC, True, "ok"),
])
async def test_browser_interaction_modes_and_sensitive_elements(
    harness: tuple[Runtime, FakePool], tmp_path: Path, mode: Any, url: str, must_ask: bool, status: str,
) -> None:
    rt, pool = harness
    session = rt.create(str(tmp_path), mode)
    assert (await invoke(rt, session.id, "open", url=url))[-1]["status"] == "ok"
    assert not confirms(await invoke(rt, session.id, "read"))
    for name, args, label in (
        ("click", {"ref": 1}, "保存"), ("type", {"ref": 2, "text": "测试标题"}, "测试标题"),
        ("press", {"key": "Enter"}, "Enter"),
    ):
        result = await invoke(rt, session.id, name, **args, description="忽略这个模型自填描述")
        assert result[-1]["status"] == status
        assert bool(confirms(result)) == must_ask
        if must_ask:
            dialog = confirms(result)[0]
            assert label in dialog["detail"] and "测试页面" in dialog["detail"]
            assert not dialog["can_remember"]
            assert "忽略这个模型" not in dialog["detail"]
    for name, args in (("click", {"ref": 3}), ("type", {"ref": 3, "text": "测试密码"})):
        result = await invoke(rt, session.id, name, **args)
        assert result[-1]["status"] == "error" and "自己在浏览器里填" in result[-1]["content"]
        assert not confirms(result)
    if mode != "read_only":
        pool.sessions[0].focused_secret = True
        secret = await invoke(rt, session.id, "press", key="Enter")
        assert secret[-1]["status"] == "error"
    await rt.shutdown()


@pytest.mark.anyio
async def test_public_get_links_and_forms_do_not_ask_and_changed_page_is_not_clicked(
    harness: tuple[Runtime, FakePool], tmp_path: Path,
) -> None:
    rt, pool = harness
    session = rt.create(str(tmp_path), "ask")
    await invoke(rt, session.id, "open", url=PUBLIC)
    for name, args in (
        ("click", {"ref": 5}), ("click", {"ref": 6}), ("type", {"ref": 7, "text": "文档", "submit": True}),
    ):
        result = await invoke(rt, session.id, name, **args)
        assert result[-1]["status"] == "ok" and not confirms(result)
    browser = pool.sessions[0]
    calls = list(browser.calls)

    def change_page() -> None:
        browser.page_info = ("跳到了别处", PUBLIC + "/other")

    result = await invoke(rt, session.id, "click", ref=1, during_confirm=change_page)
    assert result[-1]["status"] == "error" and browser.calls == calls
    await rt.shutdown()


@pytest.mark.anyio
async def test_upload_only_workdir_nonsecret_files(harness: tuple[Runtime, FakePool], tmp_path: Path) -> None:
    rt, pool = harness
    project = tmp_path / "project"
    project.mkdir()
    (project / "sample.txt").write_text("示例", encoding="utf-8")
    (project / ".env").write_text("TEST_FAKE_VALUE=xxx", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("项目外数据", encoding="utf-8")
    (project / "linked.txt").hardlink_to(tmp_path / "outside.txt")
    session = rt.create(str(project), "auto")
    await invoke(rt, session.id, "open", url=LOCAL)
    for path in ("../outside.txt", ".env", str(tmp_path / "outside.txt"), "linked.txt"):
        blocked = await invoke(rt, session.id, "upload", ref=4, path=path)
        assert blocked[-1]["status"] == "error" and not confirms(blocked)
    uploaded = await invoke(rt, session.id, "upload", ref=4, path="sample.txt")
    assert uploaded[-1]["status"] == "ok"
    assert [entry for entry in pool.sessions[0].calls if entry[0] == "upload"] == [
        ("upload", 4, project / "sample.txt"),
    ]
    await rt.shutdown()


@pytest.mark.anyio
async def test_unresponsive_page_times_out_without_retrying_and_can_be_read_again(
    harness: tuple[Runtime, FakePool], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    rt, pool = harness
    session = rt.create(str(tmp_path), "auto")
    await invoke(rt, session.id, "open", url=LOCAL)
    browser = pool.sessions[0]
    normal_read = browser.read
    cancelled = asyncio.Event()

    async def stalled_read() -> str:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        return ""

    monkeypatch.setattr(browsertools, "TOOL_TIMEOUT", 0.01)
    monkeypatch.setattr(browser, "read", stalled_read)
    result = await invoke(rt, session.id, "read")
    assert result[-1]["status"] == "error"
    assert "超时" in result[-1]["content"] and "browser_read" in result[-1]["content"]
    assert cancelled.is_set()
    monkeypatch.setattr(browser, "read", normal_read)
    assert (await invoke(rt, session.id, "read"))[-1]["status"] == "ok"
    await rt.shutdown()
