"""浏览器相关的两个技能包：

「浏览器」open_url：在用户自己的浏览器里打开网页，让用户自己看。打开网页会产生实际效果
（链接可能触发下载、退订这类操作），网址也可能来自被注入的网页内容，所以每次都要用户确认。

「网页操作」browser_*：她用自己的浏览器窗口（单独的 Edge，和用户的浏览器互不相干）去网页上办事——
打开、看、点、填、翻页、返回。只产生 GET 导航的操作（打开网页、点普通链接、提交搜索框这类 GET 表单）直接做，
其他点击和提交先问用户；密码、卡号、验证码这类输入框不碰。规则见 app/browser/session.py。
"""

import asyncio
import webbrowser
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

from ..browser import BrowserError, BrowserSession, click_needs_confirm, short_url, submit_needs_confirm
from ..browser.snapshot import Element
from . import APPROVED, Skill, Tool, ToolError

MAX_URL_CHARS = 2000
MAX_TYPE_CHARS = 2000


def check_url(args: dict[str, Any]) -> str | None:
    url = str(args.get("url", "")).strip()
    if not url:
        return "没有给网址。"
    if len(url) > MAX_URL_CHARS:
        return "网址太长了。"
    if any(c.isspace() or ord(c) < 32 for c in url):
        return "网址里不能有空格或控制字符。"
    parts = urlsplit(url)
    # 只开网页：file:、javascript:、各种自定义协议（能直接拉起本机程序）都不行
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return "只能打开 http:// 或 https:// 开头的网页。"
    return None


def browser_skill(open_url: Callable[[str], bool] = webbrowser.open) -> Skill:
    """open_url：真正打开网址的函数（测试时换成假的）"""

    async def run(args: dict[str, Any]) -> str:
        url = str(args["url"]).strip()
        if not await asyncio.to_thread(open_url, url):
            raise ToolError("没能打开浏览器。")
        return f"已经在用户的浏览器里打开了 {url}"

    tool = Tool(
        "open_url",
        "在用户自己的浏览器里打开一个网页，让用户自己看。用户说「打开……网站 / 链接给我看」时使用；"
        "你自己要读内容用 read_page，要在网页上办事（搜索、点按钮、填表）用 browser_open。",
        {"type": "object", "properties": {"url": {"type": "string", "description": "完整网址，以 http:// 或 https:// 开头"}}, "required": ["url"]},
        run,
        lambda a: f"正在打开 {urlsplit(str(a.get('url', ''))).hostname or '网页'}",
        risk="write",
        action="在浏览器里打开这个网页",
        detail=lambda a: str(a.get("url", "")).strip(),
        validate=check_url,
    )
    return Skill("browser", "浏览器", "在你的浏览器里打开网页（每次都会先问你）", [tool])


# ---------- 她自己的浏览器窗口 ----------

REF = {"type": "integer", "description": "元素编号（看网页时列出的 [编号]）"}


def browse_skill(session: BrowserSession) -> Skill:
    def ref_of(args: dict[str, Any]) -> int | None:
        try:
            return int(args.get("ref"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    def element(args: dict[str, Any]) -> Element | None:
        ref = ref_of(args)
        return session.element(ref) if ref is not None else None

    def check_ref(args: dict[str, Any]) -> str | None:
        if ref_of(args) is None:
            return "要给元素编号（ref）。"
        if element(args) is None:
            return f"没有编号 {args.get('ref')} 的元素，先用 browser_read 看一下现在的页面。"
        return None

    def page_line() -> str:
        title, url = session.page_info
        return f"页面：{title or '（无标题）'}（{short_url(url)}）"

    # 确认时给用户看的内容：来自她看到的那个元素和当前页面，不用大模型的描述
    def click_detail(args: dict[str, Any]) -> str:
        el = element(args)
        assert el is not None
        lines = [f"点击：{el.label}"]
        if el.submits and el.action:
            lines.append(f"会提交表单：{(el.method or 'post').upper()} {short_url(el.action)}")
        return "\n".join([*lines, page_line()])

    def type_detail(args: dict[str, Any]) -> str:
        el = element(args)
        assert el is not None
        text = str(args.get("text", ""))
        shown = text if len(text) <= 200 else text[:200] + "…"
        lines = [f"在{el.label}里填：{shown}", "然后提交" if el.method else "然后按回车"]
        if el.action:
            lines.append(f"会提交表单：{(el.method or 'post').upper()} {short_url(el.action)}")
        return "\n".join([*lines, page_line()])

    def click_confirm(args: dict[str, Any]) -> bool:
        el = element(args)
        return el is None or click_needs_confirm(el)

    def type_confirm(args: dict[str, Any]) -> bool:
        el = element(args)
        return bool(args.get("submit")) and (el is None or submit_needs_confirm(el))

    def check_type(args: dict[str, Any]) -> str | None:
        if problem := check_ref(args):
            return problem
        el = element(args)
        assert el is not None
        if el.kind != "input":
            return f"{el.label}不是输入框。"
        if el.sensitive:
            return "这是密码 / 卡号 / 验证码之类的输入框，我不能填，请用户自己填。"
        if len(str(args.get("text", ""))) > MAX_TYPE_CHARS:
            return "要填的内容太长了。"
        return None

    def wrap(fn: Callable[[dict[str, Any]], Awaitable[str]]) -> Callable[[dict[str, Any]], Awaitable[str]]:
        async def run(args: dict[str, Any]) -> str:
            try:
                return await fn(args)
            except BrowserError as err:
                raise ToolError(str(err)) from err

        return run

    async def open_(args: dict[str, Any]) -> str:
        return await session.open(str(args["url"]).strip())

    async def read(_: dict[str, Any]) -> str:
        return await session.read()

    async def click(args: dict[str, Any]) -> str:
        return await session.click(int(args["ref"]), approved=APPROVED.get())

    async def type_(args: dict[str, Any]) -> str:
        return await session.type(int(args["ref"]), str(args.get("text", "")), bool(args.get("submit")), approved=APPROVED.get())

    async def select(args: dict[str, Any]) -> str:
        return await session.select(int(args["ref"]), str(args.get("option", "")))

    async def scroll(args: dict[str, Any]) -> str:
        return await session.scroll(str(args.get("direction", "down")) != "up")

    async def back(_: dict[str, Any]) -> str:
        return await session.back()

    def label_of(args: dict[str, Any], fallback: str) -> str:
        el = element(args)
        return el.label if el else fallback

    def short_label(args: dict[str, Any]) -> str:
        el = element(args)
        name = (el.name if el else "") or "这个按钮"
        return name if len(name) <= 20 else name[:20] + "…"

    obj = "object"
    tools = [
        Tool(
            "browser_open",
            "在你自己的浏览器窗口里打开网页，去网页上办事（搜索、点链接、填表）。用户能看到这个窗口。"
            "返回页面标题、可以操作的元素（带编号）和正文节选，之后用编号操作。只是读一篇文章用 read_page 更快。",
            {"type": obj, "properties": {"url": {"type": "string", "description": "完整网址，以 http:// 或 https:// 开头"}}, "required": ["url"]},
            wrap(open_), lambda a: f"正在打开 {urlsplit(str(a.get('url', ''))).hostname or '网页'}",
            validate=check_url,
        ),
        Tool(
            "browser_read", "重新看一下浏览器里现在的页面（页面可能自己变了，或者编号对不上时）。",
            {"type": obj, "properties": {}}, wrap(read), lambda _: "正在看网页",
        ),
        Tool(
            "browser_click",
            "点击页面上的元素（链接、按钮、勾选框、标签页……）。点普通链接直接打开；点其他按钮会先问用户。",
            {"type": obj, "properties": {"ref": REF}, "required": ["ref"]},
            wrap(click), lambda a: f"正在点{label_of(a, '网页')}",
            risk="mixed", needs_confirm=click_confirm,
            action=lambda a: f"在网页上点「{short_label(a)}」", detail=click_detail, validate=check_ref,
        ),
        Tool(
            "browser_type",
            "在输入框里填内容；submit=true 时填完按回车提交。搜索框这类提交直接做，其他表单的提交会先问用户。"
            "密码、卡号、验证码不能填，要用户自己来。",
            {"type": obj, "properties": {
                "ref": REF,
                "text": {"type": "string", "description": "要填的内容（会替换掉原来的）"},
                "submit": {"type": "boolean", "description": "填完要不要按回车提交，默认不提交"},
            }, "required": ["ref", "text"]},
            wrap(type_), lambda a: f"正在填{label_of(a, '输入框')}",
            risk="mixed", needs_confirm=type_confirm,
            action=lambda a: f"在网页上提交「{short_label(a)}」", detail=type_detail, validate=check_type,
        ),
        Tool(
            "browser_select", "在普通下拉框里选一个选项（按选项文字）。",
            {"type": obj, "properties": {"ref": REF, "option": {"type": "string", "description": "选项的文字"}}, "required": ["ref", "option"]},
            wrap(select), lambda a: f"正在选{label_of(a, '下拉框')}", validate=check_ref,
        ),
        Tool(
            "browser_scroll", "上下翻页，看页面其他部分的内容和元素。",
            {"type": obj, "properties": {"direction": {"type": "string", "enum": ["down", "up"]}}},
            wrap(scroll), lambda a: "正在往上翻" if a.get("direction") == "up" else "正在往下翻",
        ),
        Tool(
            "browser_back", "返回上一页。", {"type": obj, "properties": {}}, wrap(back), lambda _: "正在返回上一页",
        ),
    ]
    return Skill(
        "browse", "网页操作",
        "她用自己的浏览器窗口去网页上办事：搜索、点链接、填表（点按钮、提交表单前会先问你；密码之类不碰）", tools,
    )
