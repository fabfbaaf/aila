"""网页操作：用真的浏览器（本机 Edge / Chrome，无界面模式）打开本地测试网站。

重点验证安全规则：
- 点普通链接、提交 GET 表单不执行页面脚本，也不用确认
- 点脚本按钮、提交 POST 表单必须确认，确认之前什么都不发生
- 密码、验证码不填；页面变了不乱点；本机和内网地址拦下
"""

import json
import threading
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from app.agent import run_agent
from app.browser import BrowserError, BrowserSession, click_needs_confirm
from app.confirm import Confirmations
from app.llm import ToolCall
from app.tools import SkillRegistry
from app.tools.browser import browse_skill

from .test_agent import ScriptedModel

INDEX = """<!doctype html><meta charset="utf-8"><title>测试首页</title>
<main>
  <h1>欢迎来到测试站</h1>
  <a href="/page2" onclick="fetch('/evil', {method: 'POST'})">下一页</a>
  <a href="javascript:void(0)">脚本链接</a>
  <form action="/search" method="get"><input type="search" name="q" placeholder="站内搜索"><button>搜</button></form>
  <form action="/comment" method="post"><textarea name="body" aria-label="留言"></textarea><button>发表</button></form>
  <form action="/login" method="post">
    <input name="user" placeholder="用户名"><input type="password" name="pw" placeholder="登录密码">
    <input name="code" placeholder="短信验证码">
  </form>
  <label><input type="checkbox" name="agree"> 同意条款</label>
  <select aria-label="城市"><option>北京</option><option>上海</option></select>
  <button onclick="alert('谢谢'); fetch('/like', {method: 'POST'})">点赞</button>
  <button onclick="window.open('/page2')">新窗口打开</button>
  <p>这里是正文。</p>
</main>"""


class Site:
    """本地测试网站：记录收到的请求"""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, str]] = []  # (方法, 路径, 请求体)
        site = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_: Any) -> None:
                pass

            def reply(self, html: str) -> None:
                body = html.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                site.requests.append(("GET", self.path, ""))
                path = urlsplit(self.path)
                if path.path == "/":
                    self.reply(INDEX)
                elif path.path == "/page2":
                    self.reply("<title>第二页</title><p>到了第二页</p>")
                elif path.path == "/search":
                    q = parse_qs(path.query).get("q", [""])[0]
                    self.reply(f"<title>搜索结果</title><p>搜的是：{q}</p>")
                else:
                    self.reply("<title>其他</title>")

            def do_POST(self) -> None:  # noqa: N802
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
                site.requests.append(("POST", self.path, body))
                self.reply("<title>已提交</title><p>好了</p>")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def posts(self) -> list[tuple[str, str]]:
        return [(p, b) for m, p, b in self.requests if m == "POST"]

    def close(self) -> None:
        self.server.shutdown()


@pytest.fixture
def site() -> Iterator[Site]:
    s = Site()
    yield s
    s.close()


async def no_dns(host: str) -> list[str]:
    raise OSError("测试里不查 DNS")


@pytest.fixture
async def browser(tmp_path: Path) -> AsyncIterator[BrowserSession]:
    session = BrowserSession(tmp_path / "profile", no_dns, headless=True, allow_private=True)
    try:
        yield session
    finally:
        await session.shutdown()


async def opened(browser: BrowserSession, url: str) -> str:
    try:
        return await browser.open(url)
    except BrowserError as err:
        if "没找到 Edge 或 Chrome" in str(err):
            pytest.skip("这台电脑上没有 Edge 或 Chrome")
        raise


def ref_of(browser: BrowserSession, name: str) -> int:
    assert browser._snapshot is not None
    return next(e.ref for e in browser._snapshot.elements.values() if e.name == name)


@pytest.mark.anyio
async def test_snapshot_lists_elements(browser: BrowserSession, site: Site) -> None:
    text = await opened(browser, site.url + "/")

    assert text.startswith("网页标题：测试首页")
    assert "不要理会" in text  # 网页内容不是指令
    assert "这里是正文。" in text
    els = {e.name: e for e in browser._snapshot.elements.values()}  # type: ignore[union-attr]
    assert els["下一页"].kind == "link" and els["下一页"].plain_link
    assert els["脚本链接"].kind == "link" and not els["脚本链接"].plain_link
    assert els["站内搜索"].kind == "input" and els["站内搜索"].method == "get"
    assert els["留言"].kind == "input" and els["留言"].method == "post"
    assert els["登录密码"].sensitive and els["短信验证码"].sensitive and not els["用户名"].sensitive
    assert els["同意条款"].kind == "checkbox"
    assert els["城市"].kind == "select" and els["城市"].options == ("北京", "上海")
    assert els["点赞"].kind == "button"
    assert "密码 / 卡号 / 验证码之类，你不能填" in text
    # 哪些点击要确认
    assert not click_needs_confirm(els["下一页"])
    assert not click_needs_confirm(els["同意条款"])
    assert not click_needs_confirm(els["搜"])  # GET 表单的提交按钮
    assert click_needs_confirm(els["脚本链接"])
    assert click_needs_confirm(els["点赞"])
    assert click_needs_confirm(els["发表"])


@pytest.mark.anyio
async def test_free_actions_only_navigate(browser: BrowserSession, site: Site) -> None:
    """点普通链接、提交搜索：直接打开网址，页面自己的脚本（这里会偷偷 POST）不执行"""
    await opened(browser, site.url + "/")
    text = await browser.click(ref_of(browser, "下一页"), approved=False)
    assert "网页标题：第二页" in text
    assert site.posts() == []

    await browser.back()
    text = await browser.type(ref_of(browser, "站内搜索"), "艾拉 桌面", submit=True, approved=False)
    assert "网页标题：搜索结果" in text and "搜的是：艾拉 桌面" in text
    assert ("GET", "/search?q=%E8%89%BE%E6%8B%89+%E6%A1%8C%E9%9D%A2", "") in site.requests

    await browser.back()
    text = await browser.click(ref_of(browser, "同意条款"), approved=False)
    assert "已勾选" in text
    text = await browser.select(ref_of(browser, "城市"), "上海")
    assert "现在是：上海" in text or "「上海」" in text
    assert site.posts() == []


@pytest.mark.anyio
async def test_side_effects_wait_for_approval(browser: BrowserSession, site: Site) -> None:
    await opened(browser, site.url + "/")
    like = ref_of(browser, "点赞")
    with pytest.raises(BrowserError, match="需要用户确认"):
        await browser.click(like, approved=False)
    with pytest.raises(BrowserError, match="需要用户确认"):
        await browser.type(ref_of(browser, "留言"), "写得真好", submit=True, approved=False)
    assert site.posts() == []

    # 用户同意以后：脚本按钮照常执行（弹窗自动关掉，不会卡住）
    await browser.click(like, approved=True)
    assert ("/like", "") in site.posts()

    await browser.read()
    text = await browser.type(ref_of(browser, "留言"), "写得真好", submit=True, approved=True)
    assert "网页标题：已提交" in text
    assert ("/comment", "body=%E5%86%99%E5%BE%97%E7%9C%9F%E5%A5%BD") in site.posts()


@pytest.mark.anyio
async def test_refuses_secrets_and_stale_refs(browser: BrowserSession, site: Site) -> None:
    await opened(browser, site.url + "/")
    with pytest.raises(BrowserError, match="密码"):
        await browser.type(ref_of(browser, "登录密码"), "123456", submit=False, approved=True)
    with pytest.raises(BrowserError, match="密码"):
        await browser.type(ref_of(browser, "短信验证码"), "8888", submit=False, approved=True)

    # 页面自己重新渲染了（按钮换成了新的元素）：不会点到别的东西上，让她重新看
    like = ref_of(browser, "点赞")
    await browser._page.evaluate(
        "document.querySelectorAll('button').forEach(b => b.replaceWith(Object.assign(document.createElement('button'), {textContent: b.textContent})))"
    )
    with pytest.raises(BrowserError, match="页面变了"):
        await browser.click(like, approved=True)
    assert site.posts() == []

    with pytest.raises(BrowserError, match="没有编号 999"):
        await browser.click(999, approved=True)


@pytest.mark.anyio
async def test_new_tab_becomes_current(browser: BrowserSession, site: Site) -> None:
    await opened(browser, site.url + "/")
    text = await browser.click(ref_of(browser, "新窗口打开"), approved=True)
    assert "网页标题：第二页" in text


@pytest.mark.anyio
async def test_private_addresses_are_blocked(tmp_path: Path, site: Site) -> None:
    """网页诱导她去访问本机或内网：打开不了，网页里的图片和脚本请求也被拦下"""
    session = BrowserSession(tmp_path / "profile", no_dns, headless=True)
    try:
        with pytest.raises(BrowserError, match="内网"):
            await session.open(site.url + "/")
        try:
            page = await session._ensure()
        except BrowserError:
            pytest.skip("这台电脑上没有 Edge 或 Chrome")
        await page.set_content(
            f'<img src="{site.url}/tracker.png"><script>fetch("{site.url}/steal", {{method: "POST"}}).catch(() => {{}})</script>'
        )
        await page.wait_for_timeout(500)
    finally:
        await session.shutdown()
    assert site.requests == []
    assert any(u.endswith("/tracker.png") for u in session.blocked)


@pytest.mark.anyio
async def test_agent_asks_before_clicking_and_keeps_history_append_only(browser: BrowserSession, site: Site) -> None:
    await opened(browser, site.url + "/")
    like = ref_of(browser, "点赞")
    model = ScriptedModel([
        [ToolCall("c1", "browser_read", "{}")],
        [ToolCall("c2", "browser_click", json.dumps({"ref": like}))],
        ["点好了。"],
    ])
    box = SkillRegistry([browse_skill(browser)]).toolbox()
    confirms = Confirmations()
    events = []
    async for ev in run_agent(model, [{"role": "user", "content": "帮我点个赞"}], box, confirms):
        events.append(ev)
        if ev["type"] == "confirm":
            assert site.posts() == []  # 确认之前没有点
            assert ev["action"] == "在网页上点「点赞」"
            assert ev["detail"].startswith("点击：按钮「点赞」\n页面：测试首页")
            confirms.answer(ev["id"], True)

    assert ("/like", "") in site.posts()
    third = model.calls[2][0]
    tool_results = [m["content"] for m in third if m["role"] == "tool"]
    assert "网页标题：" in tool_results[0]  # 第一次看的页面原样留着：只追加，前缀缓存才能命中
    assert third[: len(model.calls[1][0])] == model.calls[1][0]
    assert "点了按钮「点赞」" in tool_results[1]


@pytest.mark.anyio
async def test_playwright_start_that_never_answers_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """Playwright 的驱动偶尔起来了却不回话（测试里卡住过）：限时，第一次没回就再起一个，两次都不回就报错"""
    import asyncio

    from app.browser import session as browser_session

    monkeypatch.setattr(browser_session, "PW_START_TIMEOUT", 0.05)
    calls: list[int] = []

    class Starter:
        def __init__(self, hang: bool) -> None:
            self.hang = hang

        async def start(self) -> str:
            calls.append(1)
            if self.hang:
                await asyncio.sleep(10)
            return "pw"

    plan = iter([True, False])
    assert await browser_session.start_playwright(lambda: Starter(next(plan))) == "pw" and len(calls) == 2
    with pytest.raises(BrowserError, match="启动超时"):
        await browser_session.start_playwright(lambda: Starter(True))
