"""她自己的浏览器：一个单独的 Edge 窗口，用独立的配置目录（aila-ai/data/browser），
和用户自己的浏览器、登录状态互不相干。用户看得到她在做什么，也可以随时接手（比如自己登录）。

- 懒启动：第一次用到才开；10 分钟没用自动关；用户关掉窗口也没关系，下次用到再开
- 地址检查：所有请求（网页、跳转、图片、脚本发的请求）都经过这里，和 read_page 一样只许公网地址，
  免得网页里的内容诱导她去访问本机或内网（路由器管理页、本机服务）
- 不下载文件；弹窗（alert / confirm）自动关掉；新开的标签页自动切过去

哪些操作要用户确认（见 click_plan）：只产生 GET 导航的操作直接做——打开网页、点普通链接
（直接打开链接地址，不执行页面脚本）、提交 GET 表单（比如搜索框，按表单内容拼出网址再打开）。
其他点击、提交 POST 表单、在不明表单里按回车，都可能有副作用，先问用户。
密码、卡号、验证码这类输入框一律不碰，要用户自己填。
"""

import asyncio
import contextlib
import logging
import time
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from ..tools.reader import PageError, Resolver, check_public_url
from .snapshot import MAX_ELEMENTS, MAX_TEXT, SNAPSHOT_JS, Element, Snapshot

log = logging.getLogger("aila.browser")

CHANNELS = ("msedge", "chrome")  # 用本机装好的浏览器，不用另外下载
IDLE_SECONDS = 600.0
NAV_TIMEOUT_MS = 20_000
ACTION_TIMEOUT_MS = 8_000
SETTLE_MS = 1_500  # 操作后最多等这么久让页面加载完
POPUP_WAIT_MS = 800  # 点按钮后等多久看有没有新标签页
HOST_CACHE_SECONDS = 300.0
VIEWPORT = {"width": 1200, "height": 820}

ClickPlan = Literal["navigate", "toggle", "get_submit", "click"]

# 把 GET 表单的内容拼成网址（和浏览器原生提交一样），不触发页面自己的提交脚本
GET_FORM_URL_JS = r"""
(el) => {
  const form = el.form || el.closest('form')
  const type = (el.getAttribute('type') || '').toLowerCase()
  const isSubmitter = el.form && ((el.tagName === 'BUTTON' && (type === '' || type === 'submit')) ||
                                  (el.tagName === 'INPUT' && (type === 'submit' || type === 'image')))
  let data
  try { data = new FormData(form, isSubmitter ? el : undefined) } catch { data = new FormData(form) }
  const action = (isSubmitter && el.getAttribute('formaction') && el.formAction) || form.action || location.href
  const url = new URL(action, location.href)
  url.search = ''
  for (const [k, v] of data) if (typeof v === 'string') url.searchParams.append(k, v)
  return url.href
}
"""


class BrowserError(Exception):
    """操作失败，消息原样交给大模型（再由她转告用户）"""


def click_plan(el: Element) -> ClickPlan:
    if el.plain_link:
        return "navigate"
    if el.kind == "checkbox" and el.native:
        return "toggle"
    if el.submits and el.get_form:
        return "get_submit"
    return "click"


def click_needs_confirm(el: Element) -> bool:
    return click_plan(el) == "click"


def submit_needs_confirm(el: Element) -> bool:
    return not el.get_form


class BrowserSession:
    def __init__(
        self,
        profile_dir: Path,
        resolve: Resolver,
        *,
        headless: bool = False,
        channels: tuple[str, ...] = CHANNELS,
        idle_seconds: float = IDLE_SECONDS,
        allow_private: bool = False,  # 只给测试用（测试网页在本机）
    ) -> None:
        self._profile = profile_dir
        self._resolve = resolve
        self._headless = headless
        self._channels = channels
        self._idle = idle_seconds
        self._allow_private = allow_private
        self._lock = asyncio.Lock()
        self._pw: Any = None
        self._context: Any = None
        self._page: Any = None
        self._snapshot: Snapshot | None = None
        self._hosts: dict[str, tuple[str | None, float]] = {}
        self._last_used = time.monotonic()
        self._watchdog: asyncio.Task[None] | None = None
        self.blocked: list[str] = []  # 拦下的请求（排查和测试用）

    # ---------- 给工具用的 ----------

    @property
    def is_open(self) -> bool:
        return self._context is not None

    def element(self, ref: int) -> Element | None:
        """最近一次看到的页面里，这个编号的元素（确认时显示用）"""
        return self._snapshot.elements.get(ref) if self._snapshot else None

    @property
    def page_info(self) -> tuple[str, str]:
        return (self._snapshot.title, self._snapshot.url) if self._snapshot else ("", "")

    async def open(self, url: str) -> str:
        async with self._lock:
            await self._check_nav(url)
            page = await self._ensure()
            await self._goto(page, url)
            return await self._snap()

    async def read(self) -> str:
        async with self._lock:
            if not self.is_open:
                raise BrowserError("浏览器还没打开，先用 browser_open 打开网页。")
            return await self._snap()

    async def click(self, ref: int, approved: bool) -> str:
        async with self._lock:
            el = self._known(ref)
            if el.kind == "file":
                raise BrowserError("上传文件要用户自己来。")
            plan = click_plan(el)
            if plan == "click" and not approved:
                raise BrowserError("这个操作需要用户确认。")
            page = await self._ensure()
            loc = await self._locate(page, el)
            if plan == "navigate":
                await self._check_nav(el.href or "")
                await self._goto(page, el.href or "")
            elif plan == "get_submit":
                await self._submit_get(page, loc)
            else:
                # 按钮可能在新标签页里打开东西：点之前就开始等，等到了就切过去（_on_page）
                popup = asyncio.ensure_future(self._context.wait_for_event("page", timeout=POPUP_WAIT_MS))
                await loc.click()
                with contextlib.suppress(Exception):
                    await popup
                await self._settle(await self._ensure())
            return f"点了{el.label}。现在的页面：\n" + await self._snap()

    async def type(self, ref: int, text: str, submit: bool, approved: bool) -> str:
        async with self._lock:
            el = self._known(ref)
            if el.kind != "input":
                raise BrowserError(f"{el.label}不是输入框。")
            if el.sensitive:
                raise BrowserError("这是密码 / 卡号 / 验证码之类的输入框，我不能填，请用户自己填。")
            if submit and submit_needs_confirm(el) and not approved:
                raise BrowserError("这个操作需要用户确认。")
            page = await self._ensure()
            loc = await self._locate(page, el)
            await loc.fill(text)
            if submit:
                if el.get_form:
                    await self._submit_get(page, loc)
                elif el.method:
                    # 在表单里：像点提交按钮一样提交（多行输入框里按回车只是换行）
                    await loc.evaluate("el => (el.form || el.closest('form')).requestSubmit()")
                    await self._settle(page)
                else:
                    await loc.press("Enter")  # 不在表单里（聊天框之类）：按回车
                    await self._settle(page)
            done = f"在{el.label}里填好了" + ("并提交了" if submit else "")
            return f"{done}。现在的页面：\n" + await self._snap()

    async def select(self, ref: int, option: str) -> str:
        async with self._lock:
            el = self._known(ref)
            if el.kind != "select" or el.tag != "select":
                raise BrowserError(f"{el.label}不是普通下拉框；如果是自定义的下拉菜单，用 browser_click 点开再点选项。")
            page = await self._ensure()
            loc = await self._locate(page, el)
            try:
                await loc.select_option(label=option)
            except Exception:
                try:
                    await loc.select_option(value=option)
                except Exception as err:
                    raise BrowserError(f"{el.label}里没有「{option}」这个选项。") from err
            await self._settle(page)
            return f"在{el.label}里选了「{option}」。现在的页面：\n" + await self._snap()

    async def scroll(self, down: bool) -> str:
        async with self._lock:
            if not self.is_open:
                raise BrowserError("浏览器还没打开，先用 browser_open 打开网页。")
            page = await self._ensure()
            await page.evaluate("(down) => window.scrollBy(0, (down ? 0.8 : -0.8) * window.innerHeight)", down)
            await self._settle(page, 600)
            return await self._snap()

    async def back(self) -> str:
        async with self._lock:
            if not self.is_open:
                raise BrowserError("浏览器还没打开。")
            page = await self._ensure()
            if await page.go_back(wait_until="domcontentloaded") is None:
                raise BrowserError("已经是第一页了，没法再返回。")
            await self._settle(page)
            return await self._snap()

    async def close(self) -> None:
        async with self._lock:
            await self._close_context()

    async def shutdown(self) -> None:
        await self.close()
        if self._pw is not None:
            with contextlib.suppress(Exception):
                await self._pw.stop()
            self._pw = None

    # ---------- 启动和关闭 ----------

    async def _ensure(self) -> Any:
        self._last_used = time.monotonic()
        if self._context is None:
            await self._launch()
        if self._page is None or self._page.is_closed():
            pages = [p for p in self._context.pages if not p.is_closed()]
            self._page = pages[-1] if pages else await self._context.new_page()
        return self._page

    async def _launch(self) -> None:
        from playwright.async_api import async_playwright  # 用到浏览器才加载

        if self._pw is None:
            self._pw = await async_playwright().start()
        self._profile.mkdir(parents=True, exist_ok=True)
        last: Exception | None = None
        for channel in self._channels:
            try:
                ctx = await self._pw.chromium.launch_persistent_context(
                    str(self._profile), channel=channel, headless=self._headless, accept_downloads=False,
                    viewport=VIEWPORT, args=["--no-first-run", "--no-default-browser-check"],
                )
                log.info("浏览器已打开（%s）", channel)
                break
            except Exception as err:  # 没装这个浏览器，换下一个
                last = err
        else:
            raise BrowserError("打不开浏览器（这台电脑上没找到 Edge 或 Chrome）。") from last

        ctx.set_default_timeout(ACTION_TIMEOUT_MS)
        ctx.set_default_navigation_timeout(NAV_TIMEOUT_MS)
        await ctx.route("**/*", self._guard)
        ctx.on("page", self._on_page)
        ctx.on("close", lambda c: self._forget(c))
        self._context = ctx
        for p in ctx.pages:
            self._on_page(p)
        self._watchdog = asyncio.create_task(self._idle_watch())

    def _on_page(self, page: Any) -> None:
        """新标签页（链接在新窗口打开、脚本 window.open）：切过去，弹窗自动关掉"""

        async def dismiss(dialog: Any) -> None:
            with contextlib.suppress(Exception):
                await dialog.dismiss()

        page.on("dialog", dismiss)
        self._page = page

    def _forget(self, ctx: Any) -> None:
        """窗口被关掉了（用户关的，或者空闲太久我们关的）"""
        if self._context is ctx:
            self._context = self._page = self._snapshot = None

    async def _close_context(self) -> None:
        ctx, self._context = self._context, None
        self._page = self._snapshot = None
        if self._watchdog and self._watchdog is not asyncio.current_task():
            self._watchdog.cancel()
        self._watchdog = None
        if ctx is not None:
            with contextlib.suppress(Exception):
                await ctx.close()
            log.info("浏览器已关闭")

    async def _idle_watch(self) -> None:
        while self._context is not None:
            await asyncio.sleep(min(30.0, self._idle / 2))
            if self._context is not None and time.monotonic() - self._last_used > self._idle and not self._lock.locked():
                log.info("浏览器 %.0f 秒没用，关掉", self._idle)
                await self.close()
                return

    # ---------- 地址检查 ----------

    async def _guard(self, route: Any, request: Any) -> None:
        url = request.url
        if urlsplit(url).scheme in ("data", "blob", "about"):
            await route.continue_()
            return
        try:
            await self._check(url)
        except PageError:
            self.blocked.append(url)
            log.info("拦下了 %s", url[:120])
            await route.abort("blockedbyclient")
            return
        await route.continue_()

    async def _check(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            raise PageError("只能打开 http 或 https 网页")
        if self._allow_private:
            return
        host = (parts.hostname or "").lower()
        now = time.monotonic()
        cached = self._hosts.get(host)
        if cached and cached[1] > now:
            if cached[0] is not None:
                raise PageError(cached[0])
            return
        try:
            await check_public_url(url, self._resolve)
        except PageError as err:
            if "内网" in str(err):  # 查不到地址这类临时问题不缓存
                self._hosts[host] = (str(err), now + HOST_CACHE_SECONDS)
            raise
        self._hosts[host] = (None, now + HOST_CACHE_SECONDS)

    async def _check_nav(self, url: str) -> None:
        try:
            await self._check(url)
        except PageError as err:
            raise BrowserError(f"打不开：{err}") from err

    # ---------- 页面操作 ----------

    async def _goto(self, page: Any, url: str) -> None:
        from playwright.async_api import Error as PlaywrightError

        try:
            await page.goto(url, wait_until="domcontentloaded")
        except PlaywrightError as err:
            msg = str(err)
            if "ERR_BLOCKED_BY_CLIENT" in msg:
                raise BrowserError("打不开：不能访问本机或内网地址") from err
            if "Timeout" not in msg:  # 超时的话页面多半已经显示了一部分，接着看
                raise BrowserError(f"网页打不开（{msg.splitlines()[0][:120]}）") from err
        await self._settle(page)

    async def _settle(self, page: Any, ms: int = SETTLE_MS) -> None:
        """等页面加载、脚本渲染完一点；等不到也不要紧"""
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT_MS)
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=ms)

    async def _submit_get(self, page: Any, loc: Any) -> None:
        url = await loc.evaluate(GET_FORM_URL_JS)
        await self._check_nav(url)
        await self._goto(page, url)

    def _known(self, ref: int) -> Element:
        el = self.element(ref)
        if el is None:
            raise BrowserError(f"没有编号 {ref} 的元素，先用 browser_read 看一下现在的页面。")
        return el

    async def _locate(self, page: Any, el: Element) -> Any:
        """确认要操作的还是她看到的那个元素：页面重新渲染过就让她重新看"""
        loc = page.locator(f'[data-aila-ref="{el.ref}"]')
        changed = BrowserError(f"页面变了，编号 {el.ref} 已经不是原来那个{el.label}，先用 browser_read 重新看一下页面。")
        if await loc.count() != 1:
            raise changed
        live = await loc.evaluate("el => [el.tagName.toLowerCase(), el.tagName === 'A' ? el.href : null]")
        if live != [el.tag, el.href]:
            raise changed
        return loc

    async def _snap(self) -> str:
        page = await self._ensure()
        for attempt in range(2):
            try:
                raw = await page.evaluate(SNAPSHOT_JS, [MAX_ELEMENTS, MAX_TEXT])
                break
            except Exception:  # 页面正好在跳转，等它加载完再看一次
                if attempt:
                    raise BrowserError("页面还在加载，稍后再看一次。") from None
                await self._settle(page)
                page = await self._ensure()
        self._snapshot = Snapshot.parse(raw)
        self._last_used = time.monotonic()
        return self._snapshot.render()
