"""她自己的浏览器：一个单独的 Edge 窗口，用独立的配置目录（aila-ai/data/browser），
和用户自己的浏览器、登录状态互不相干。用户看得到她在做什么，也可以随时接手（比如自己登录）。

- 懒启动：第一次用到才开；10 分钟没用自动关；交给用户的标签页在使用期间保持打开
- 地址检查：所有请求（网页、跳转、图片、脚本发的请求）都经过这里，和 read_page 一样只许公网地址，
  免得网页里的内容诱导她去访问本机或内网（路由器管理页、本机服务）
- 不下载文件；弹窗（alert / confirm）自动关掉；新开的标签页自动切过去

哪些操作要用户确认（见 click_plan）：只产生 GET 导航的操作直接做——打开网页、点普通链接
（直接打开链接地址，不执行页面脚本）、提交 GET 表单（比如搜索框，按表单内容拼出网址再打开）。
其他点击、提交 POST 表单、在不明表单里按回车，都可能有副作用，先问用户。
密码、卡号、验证码这类输入框一律不碰，要用户自己填。

编程任务共用一个 Edge 进程，每个会话有自己的浏览器上下文和本机端口许可。
独立网络出口用同一套地址判断拦住重定向和 WebSocket，避免它们绕过 Playwright 路由。
"""

import asyncio
import contextlib
import ipaddress
import logging
import re
import time
from collections.abc import Collection
from pathlib import Path
from typing import Any, Literal
from urllib.parse import SplitResult, urlsplit

from ..tools.reader import PageError, Resolver, check_public_url
from .proxy import BrowserProxy
from .snapshot import MAX_ELEMENTS, MAX_TEXT, SENSITIVE_JS, SNAPSHOT_JS, Element, Snapshot

log = logging.getLogger("aila.browser")

CHANNELS = ("msedge", "chrome")  # 用本机装好的浏览器，不用另外下载
IDLE_SECONDS = 600.0
NAV_TIMEOUT_MS = 20_000
ACTION_TIMEOUT_MS = 8_000
SETTLE_MS = 1_500  # 操作后最多等这么久让页面加载完
POPUP_WAIT_MS = 800  # 点按钮后等多久看有没有新标签页
PW_START_TIMEOUT = 30  # 秒：Playwright 驱动起来、停下最多等多久
LAUNCH_TIMEOUT_MS = 30_000  # 浏览器窗口起来最多等多久
HOST_CACHE_SECONDS = 300.0
VIEWPORT = {"width": 1200, "height": 820}
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
SENSITIVE_INPUT = "这是密码 / 卡号 / 验证码之类的输入框，我不能操作，要用户自己来。"

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


async def start_playwright(factory: Any) -> Any:
    """起 Playwright 的驱动进程。Windows 上它偶尔起来了却不回话，start() 就一直等下去（测试里卡过）：
    限时，超时就再起一个新的，还不行就报错"""
    for attempt in range(2):
        try:
            return await asyncio.wait_for(factory().start(), PW_START_TIMEOUT)
        except TimeoutError:
            log.warning("浏览器组件启动超时（第 %d 次）", attempt + 1)
    raise BrowserError("浏览器组件启动超时，过一会儿再试。")


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
        profile_dir: Path | None,
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
        self._user_pages: set[Any] = set()
        self._opening_user_page = False
        self._snapshot: Snapshot | None = None
        self._snapshot_page: Any = None
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

    async def open_for_user(self, url: str) -> None:
        """在同一份登录资料里新开一页，由用户操作；不读取正文或表单。"""
        from playwright.async_api import Error as PlaywrightError

        async with self._lock:
            await self._check_nav(url)
            if self._context is None:
                await self._launch()
            self._snapshot = self._snapshot_page = None
            page = None
            opened = False
            self._opening_user_page = True
            try:
                page = await self._context.new_page()
                await page.bring_to_front()
                await page.goto(url, wait_until="commit")
                opened = True
            except PlaywrightError:
                raise BrowserError("网页没能打开，看看浏览器窗口或网络连接。") from None
            finally:
                self._opening_user_page = False
                if not opened and page is not None:
                    with contextlib.suppress(Exception):
                        await page.close()

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
            before = page.url
            # 从浏览器的前进后退缓存（bfcache）里恢复时 go_back 也返回 None，只看返回值会误判成「已经是第一页」
            if await page.go_back(wait_until="domcontentloaded") is None and page.url == before:
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
                await asyncio.wait_for(self._pw.stop(), PW_START_TIMEOUT)  # 关不掉也别一直等
            self._pw = None

    # ---------- 启动和关闭 ----------

    async def _ensure(self) -> Any:
        if self._user_pages:
            raise BrowserError("浏览器已交给用户，关掉手动操作的标签页后可以继续。")
        self._last_used = time.monotonic()
        if self._context is None:
            await self._launch()
        if self._page is None or self._page.is_closed():
            pages = [p for p in self._context.pages if not p.is_closed()]
            self._page = pages[-1] if pages else await self._context.new_page()
        return self._page

    async def _launch(self) -> None:
        ctx = await self._new_context()
        try:
            ctx.set_default_timeout(ACTION_TIMEOUT_MS)
            ctx.set_default_navigation_timeout(NAV_TIMEOUT_MS)
            await ctx.route("**/*", self._guard)
            ctx.on("page", self._on_page)
            ctx.on("close", lambda c: self._forget(c))
            self._context = ctx
            for p in ctx.pages:
                self._on_page(p)
            self._watchdog = asyncio.create_task(self._idle_watch())
        except BaseException:
            await ctx.close()
            raise

    async def _new_context(self) -> Any:
        from playwright.async_api import async_playwright  # 用到浏览器才加载

        if self._pw is None:
            self._pw = await start_playwright(async_playwright)
        assert self._profile is not None
        self._profile.mkdir(parents=True, exist_ok=True)
        last: Exception | None = None
        for channel in self._channels:
            try:
                ctx = await self._pw.chromium.launch_persistent_context(
                    str(self._profile), channel=channel, headless=self._headless, accept_downloads=False,
                    viewport=VIEWPORT, args=["--no-first-run", "--no-default-browser-check"],
                    timeout=LAUNCH_TIMEOUT_MS,
                )
                log.info("浏览器已打开（%s）", channel)
                return ctx
            except Exception as err:  # 没装这个浏览器，换下一个
                last = err
        raise BrowserError("打不开浏览器（这台电脑上没找到 Edge 或 Chrome）。") from last

    def _on_page(self, page: Any) -> None:
        """新标签页（链接在新窗口打开、脚本 window.open）：切过去，弹窗自动关掉"""
        if self._opening_user_page or self._user_pages:
            # 登录可能经过新窗口，人工操作期间的新页都归用户，直到用户关掉它们。
            self._user_pages.add(page)
            page.once("close", self._user_page_closed)
            return

        async def dismiss(dialog: Any) -> None:
            with contextlib.suppress(Exception):
                await dialog.dismiss()

        page.on("dialog", dismiss)
        self._page = page

    def _user_page_closed(self, page: Any) -> None:
        self._user_pages.discard(page)
        self._last_used = time.monotonic()

    def _forget(self, ctx: Any) -> None:
        """窗口被关掉了（用户关的，或者空闲太久我们关的）"""
        if self._context is ctx:
            self._context = self._page = self._snapshot = self._snapshot_page = None
            self._user_pages.clear()

    async def _close_context(self) -> None:
        ctx, self._context = self._context, None
        self._page = self._snapshot = self._snapshot_page = None
        self._user_pages.clear()
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
            if (self._context is not None and not self._user_pages and not self._lock.locked()
                    and time.monotonic() - self._last_used > self._idle):
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
        self._snapshot_page = page
        self._last_used = time.monotonic()
        return self._snapshot.render()


def _coding_url(url: str) -> tuple[SplitResult, int]:
    try:
        parts = urlsplit(url)
        explicit_port = parts.port
    except ValueError as err:
        raise BrowserError("网址或端口格式不对。") from err
    if (parts.scheme not in ("http", "https") or not parts.hostname or parts.username is not None
            or "\\" in url or any(c.isspace() or ord(c) < 32 for c in url)):
        raise BrowserError("只能打开格式完整的 http 或 https 网页。")
    if explicit_port == 0 or parts.netloc.endswith(":"):
        raise BrowserError("端口必须在 1 到 65535 之间。")
    return parts, explicit_port or (443 if parts.scheme == "https" else 80)


def localhost_port(url: str) -> int | None:
    """识别可向用户申请授权的本机 URL，其他主机仍需走公网检查。"""
    parts, port = _coding_url(url)
    return port if parts.hostname in LOOPBACK_HOSTS else None


class CodingBrowserPool:
    """编程会话共享一个浏览器进程，各自持有独立上下文和网络出口。"""

    def __init__(self, resolve: Resolver, *, forbidden_ports: Collection[int], headless: bool = False,
                 channels: tuple[str, ...] = CHANNELS, idle_seconds: float = IDLE_SECONDS) -> None:
        self.resolve = resolve
        self.forbidden_ports = frozenset(forbidden_ports) | {8790}
        self.headless = headless
        self.channels = channels
        self.idle_seconds = idle_seconds
        self._lock = asyncio.Lock()
        self._pw: Any = None
        self._browser: Any = None
        self._contexts: dict[Any, BrowserProxy] = {}

    def session(self) -> "CodingBrowser":
        return CodingBrowser(self)

    async def _context(self, proxy: BrowserProxy) -> Any:
        async with self._lock:
            try:
                if self._browser is None or not self._browser.is_connected():
                    self._browser = await self._start_browser()
                server = await proxy.start()
                ctx = await self._browser.new_context(
                    proxy={"server": server, "bypass": "<-loopback>"}, service_workers="block",
                    accept_downloads=False, viewport=VIEWPORT,
                )
            except BaseException:
                await proxy.close()
                if not self._contexts:
                    await self._stop_browser()
                raise
            self._contexts[ctx] = proxy
            ctx.on("close", lambda closed: asyncio.create_task(self._release(closed)))
            return ctx

    async def _start_browser(self) -> Any:
        from playwright.async_api import async_playwright

        if self._pw is None:
            self._pw = await start_playwright(async_playwright)
        last: Exception | None = None
        for channel in self.channels:
            try:
                browser = await self._pw.chromium.launch(
                    channel=channel, headless=self.headless,
                    args=["--no-first-run", "--no-default-browser-check", "--disable-quic",
                          "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"],
                    timeout=LAUNCH_TIMEOUT_MS,
                )
                log.info("编程浏览器已打开（%s）", channel)
                return browser
            except Exception as err:
                last = err
        raise BrowserError("打不开浏览器（这台电脑上没找到 Edge 或 Chrome）。") from last

    async def _release(self, ctx: Any) -> None:
        async with self._lock:
            proxy = self._contexts.pop(ctx, None)
            if proxy is None:
                return
            await proxy.close()
            if not self._contexts:
                await self._stop_browser()

    async def _stop_browser(self) -> None:
        browser, self._browser = self._browser, None
        if browser is not None:
            with contextlib.suppress(Exception):
                await browser.close()
        if self._pw is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._pw.stop(), PW_START_TIMEOUT)  # 关不掉也别一直等
            self._pw = None

    async def shutdown(self) -> None:
        async with self._lock:
            for proxy in self._contexts.values():
                await proxy.close()
            self._contexts.clear()
            await self._stop_browser()


LIVE_ELEMENT_JS = r"""(el) => {
  const sensitive = """ + SENSITIVE_JS + r"""
  const tag = el.tagName.toLowerCase()
  const type = (el.getAttribute('type') || (tag === 'input' ? 'text' : '')).toLowerCase()
  const form = el.form || el.closest('form')
  const submits = !!form && ((tag === 'button' && (type === '' || type === 'submit')) ||
                             (tag === 'input' && (type === 'submit' || type === 'image')))
  const method = form ? ((submits && el.getAttribute('formmethod')) || form.getAttribute('method') || 'get').toLowerCase() : null
  const action = form ? ((submits && el.getAttribute('formaction') && el.formAction) || form.action || location.href) : null
  return [type, method, action, sensitive(el, type)]
}"""

FOCUSED_JS = r"""() => {
  const sensitive = """ + SENSITIVE_JS + r"""
  let el = document.activeElement
  while (el?.shadowRoot?.activeElement) el = el.shadowRoot.activeElement
  if (!el) return ''
  const type = (el.getAttribute('type') || '').toLowerCase()
  if (['IFRAME', 'FRAME', 'EMBED', 'OBJECT'].includes(el.tagName)) return 'frame'
  if (type === 'file') return 'file'
  return sensitive(el, type) ? 'sensitive' : ''
}"""

# 在独立的表单里组装 GET 导航，原页面收不到输入或 formdata 事件。
CODING_GET_FORM_URL_JS = r"""(el, value) => {
  const sensitive = """ + SENSITIVE_JS + r"""
  const form = el.form || el.closest('form')
  const type = (el.getAttribute('type') || '').toLowerCase()
  const isSubmitter = el.form && ((el.tagName === 'BUTTON' && (type === '' || type === 'submit')) ||
                                  (el.tagName === 'INPUT' && (type === 'submit' || type === 'image')))
  const action = (isSubmitter && el.getAttribute('formaction') && el.formAction) || form.action || location.href
  const copy = document.implementation.createHTMLDocument().createElement('form')
  let submitter
  for (const control of form.elements) {
    if (!['INPUT', 'BUTTON', 'TEXTAREA', 'SELECT'].includes(control.tagName) || control.matches(':disabled')) continue
    if (control.value && ['INPUT', 'TEXTAREA', 'SELECT'].includes(control.tagName) &&
        sensitive(control, (control.getAttribute('type') || '').toLowerCase())) return null
    const field = control.cloneNode(true)
    field.removeAttribute('form')
    if (control.tagName === 'SELECT') {
      for (const [index, option] of [...control.options].entries()) field.options[index].selected = option.selected
    } else if (control.type !== 'file') {
      field.value = control === el && value !== null ? value : control.value
      if (control.tagName === 'INPUT') field.checked = control.checked
    }
    copy.append(field)
    if (control === el && isSubmitter) submitter = field
  }
  const url = new URL(action, location.href)
  url.search = ''
  for (const [key, item] of new FormData(copy, submitter)) {
    if (typeof item === 'string') url.searchParams.append(key, item)
  }
  return url.href
}"""


class CodingBrowser(BrowserSession):
    def __init__(self, pool: CodingBrowserPool) -> None:
        super().__init__(None, pool.resolve, idle_seconds=pool.idle_seconds)
        self._pool = pool
        self._ports: set[int] = set()
        self._public_ips: dict[str, tuple[list[str], float]] = {}
        self._tabs: dict[Any, int] = {}
        self._next_tab = 1

    def check_port(self, port: int, *, require_grant: bool = False) -> None:
        if not 0 < port <= 65535:
            raise BrowserError("端口必须在 1 到 65535 之间。")
        if port in self._pool.forbidden_ports:
            raise BrowserError(f"不能访问艾拉服务使用的本机端口 {port}。")
        if require_grant and port not in self._ports:
            raise BrowserError(f"本机端口 {port} 还没有得到允许。")

    def port_allowed(self, port: int) -> bool:
        return port in self._ports and port not in self._pool.forbidden_ports

    def allow_port(self, port: int) -> None:
        self.check_port(port)
        self._ports.add(port)

    async def check_url(self, url: str) -> None:
        await self._check_nav(url)

    async def _new_context(self) -> Any:
        return await self._pool._context(BrowserProxy(self._proxy_addresses))

    async def _close_context(self) -> None:
        ctx = self._context
        await super()._close_context()
        if ctx is not None:
            await self._pool._release(ctx)
        self._tabs.clear()
        self._snapshot_page = None

    def _on_page(self, page: Any) -> None:
        self._tabs[page] = self._next_tab
        self._next_tab += 1
        self._snapshot = self._snapshot_page = None
        super()._on_page(page)

        def navigated(frame: Any) -> None:
            if page is self._page and frame is page.main_frame:
                self._snapshot = self._snapshot_page = None

        def closed(_: Any) -> None:
            self._tabs.pop(page, None)
            if page is self._snapshot_page:
                self._snapshot = self._snapshot_page = None

        page.on("framenavigated", navigated)
        page.on("close", closed)

    async def _check(self, url: str) -> None:
        try:
            parts, port = _coding_url(url)
        except BrowserError as err:
            raise PageError(str(err)) from err
        await self._addresses(parts.hostname or "", port)

    async def _proxy_addresses(self, host: str, port: int) -> list[str]:
        try:
            return await self._addresses(host.removeprefix("[").removesuffix("]").lower(), port)
        except PageError as err:
            raise PermissionError(str(err)) from err

    async def _addresses(self, host: str, port: int) -> list[str]:
        if not 0 < port <= 65535 or not re.fullmatch(r"[a-z0-9.:%_-]+", host):
            raise PageError("网址或端口格式不对。")
        if host in LOOPBACK_HOSTS:
            try:
                self.check_port(port, require_grant=True)
            except BrowserError as err:
                raise PageError(str(err)) from err
            return ["127.0.0.1", "::1"] if host == "localhost" else [host]
        now = time.monotonic()
        cached = self._public_ips.get(host)
        if cached and cached[1] > now:
            return cached[0]
        addresses: list[str] = []

        async def resolve(name: str) -> list[str]:
            addresses.extend(await self._resolve(name))
            return addresses

        netloc = f"[{host}]" if ":" in host else host
        try:
            await check_public_url(f"http://{netloc}:{port}", resolve)
            verified = [str(ipaddress.ip_address(a.split("%")[0])) for a in addresses] if addresses else [host]
        except ValueError as err:
            raise PageError("网址或地址格式不对。") from err
        self._public_ips[host] = (verified, now + HOST_CACHE_SECONDS)
        return verified

    def _snapshot_matches(self, expected_url: str | None = None) -> bool:
        return (self._snapshot is not None and self._snapshot_page is self._page
                and not self._page.is_closed() and self._snapshot.url == self._page.url
                and (expected_url is None or self._snapshot.url == expected_url))

    def _known(self, ref: int) -> Element:
        if not self._snapshot_matches():
            raise BrowserError("页面变了，先用 browser_read 重新看一下页面。")
        return super()._known(ref)

    async def _locate(self, page: Any, el: Element, *, can_view_sensitive: bool = False) -> Any:
        loc = await super()._locate(page, el)
        live = await loc.evaluate(LIVE_ELEMENT_JS)
        if not self._snapshot_matches() or live[:3] != [el.type, el.method, el.action]:
            raise BrowserError("页面变了，先用 browser_read 重新看一下页面。")
        if not can_view_sensitive and (el.sensitive or (el.kind in ("input", "select", "file") and live[3])):
            raise BrowserError(SENSITIVE_INPUT)
        return loc

    async def _submit_get(self, page: Any, loc: Any, text: str | None = None) -> None:
        url = await loc.evaluate(CODING_GET_FORM_URL_JS, text)
        if url is None:
            raise BrowserError("表单里填了敏感信息，要用户自己提交。")
        await self._check_nav(url)
        await self._goto(page, url)

    async def type(self, ref: int, text: str, submit: bool, approved: bool) -> str:
        if not submit or not self._known(ref).get_form:
            return await super().type(ref, text, submit, approved)
        async with self._lock:
            el = self._known(ref)
            if el.kind != "input":
                raise BrowserError(f"{el.label}不是输入框。")
            if not el.get_form:
                raise BrowserError("表单变了，先用 browser_read 重新看一下页面。")
            page = self._page
            loc = await self._locate(page, el)
            await self._submit_get(page, loc, text)
            return f"在{el.label}里填好了并提交了。现在的页面：\n" + await self._snap()

    async def press(self, key: str, *, expected_url: str | None = None) -> str:
        async with self._lock:
            if not self._snapshot_matches(expected_url):
                raise BrowserError("页面变了，先用 browser_read 重新看一下页面。")
            if not key or len(key) > 64 or any(c.isspace() for c in key):
                raise BrowserError("给一个按键，如 Enter、Tab 或 Ctrl+A。")
            page = self._page
            problem = await page.evaluate(FOCUSED_JS)
            if problem == "sensitive":
                raise BrowserError(SENSITIVE_INPUT)
            if problem in ("file", "frame"):
                raise BrowserError("焦点在文件输入框或内嵌页面里，要用户自己按键。")
            if not self._snapshot_matches(expected_url):
                raise BrowserError("页面变了，先用 browser_read 重新看一下页面。")
            key = re.sub(r"(^|\+)Ctrl(?=\+|$)", r"\1Control", key, flags=re.I)
            from playwright.async_api import Error as PlaywrightError

            try:
                await page.keyboard.press(key)
            except PlaywrightError as err:
                raise BrowserError("按不了这个键，确认一下按键名称。") from err
            await self._settle(page)
            return await self._snap()

    async def tabs(self, action: str = "list", tab: int | None = None) -> str:
        async with self._lock:
            if action == "list":
                lines = [f"[{number}] {'当前：' if page is self._page else ''}{page.url}"
                         for page, number in self._tabs.items() if not page.is_closed()]
                return "\n".join(lines) if lines else "这个任务还没有打开标签页。"
            if action not in ("switch", "close"):
                raise BrowserError("只能列出、切换或关闭标签页。")
            page = next((p for p, number in self._tabs.items() if number == tab and not p.is_closed()), None)
            if page is None:
                raise BrowserError("这个任务没有编号对应的标签页。")
            if action == "close":
                await page.close()
                return f"已关闭标签页 {tab}。"
            await page.bring_to_front()
            self._page = page
            return await self._snap()

    async def upload(self, ref: int, path: Path) -> str:
        async with self._lock:
            el = self._known(ref)
            if el.kind != "file" or el.type != "file":
                raise BrowserError(f"{el.label}不是文件输入框。")
            loc = await self._locate(self._page, el)
            await loc.set_input_files(str(path))
            return await self._snap()

    async def screenshot(self, ref: int | None = None) -> tuple[bytes, str]:
        async with self._lock:
            if not self.is_open:
                raise BrowserError("浏览器还没打开，先打开网页。")
            page = await self._ensure()
            if ref is None:
                image = await page.screenshot(type="png")
            else:
                loc = await self._locate(page, self._known(ref), can_view_sensitive=True)
                image = await loc.screenshot(type="png")
            return image, await self._snap()
