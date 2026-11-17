"""编程任务的浏览器工具。每个会话有自己的标签页，权限用代码算出的页面和元素判断。

第一次访问本机端口时能按项目记住；图片留在会话的数据目录，模型只拿到文字概况。
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from ..browser.session import BrowserError, CodingBrowser, CodingBrowserPool, click_plan, localhost_port
from ..browser.snapshot import Element, short_url
from ..tools.reader import make_resolver
from .tools import CodingTool, Outcome, _int, _obj, _str
from .workspace import Workspace, WorkspaceError

MAX_URL = 2000
MAX_INPUT = 2000
MAX_SHOT = 16 * 1024 * 1024
TOOL_TIMEOUT = 45.0
PNG = b"\x89PNG\r\n\x1a\n"
SENSITIVE = "这是密码、卡号或验证码之类的输入框，让用户自己在浏览器里填。"
REF = _int("browser_read 列出的元素编号")
Args = dict[str, Any]
Action = Callable[[], Awaitable[str | Outcome]]


@cache
def browser_pool() -> CodingBrowserPool:
    from ..deps import http_client, settings

    return CodingBrowserPool(make_resolver(http_client), forbidden_ports={settings.port, 8765, 8790})


@dataclass(frozen=True)
class Plan:
    action: str
    run: Action
    kind: str = "browser_read"
    detail: str = ""
    key: str = ""


class BrowserAccess:
    def __init__(self, workspace: Workspace, session_path: Path) -> None:
        self.workspace = workspace
        self.folder = session_path.with_suffix(".shots")
        self.browser: CodingBrowser = browser_pool().session()

    async def close(self) -> None:
        await self.browser.close()

    def tools(self) -> list[CodingTool]:
        return [
            self._tool("open", "打开网页，返回标题、元素编号和正文节选。首次打开本机开发服务器的端口要用户确认。",
                       _obj({"url": _str("完整的 http:// 或 https:// 网址")}, ["url"]), self._open),
            self._tool("read", "看当前标签页，刷新元素编号和网页内容。", _obj({}, []),
                       lambda _: Plan("看网页", self.browser.read)),
            self._tool("click", "点击网页上的元素。普通链接和 GET 表单可以直接导航。",
                       _obj({"ref": REF}, ["ref"]), self._click),
            self._tool("type", "填写输入框，submit=true 时提交。密码、卡号、验证码让用户自己填。",
                       _obj({"ref": REF, "text": _str("替换成的文字"), "submit": {"type": "boolean"}}, ["ref", "text"]),
                       self._type),
            self._tool("select", "按文字选择普通下拉框的选项。",
                       _obj({"ref": REF, "option": _str("选项文字")}, ["ref", "option"]), self._select),
            self._tool("scroll", "上下翻页。", _obj({"direction": {"type": "string", "enum": ["down", "up"]}}, []),
                       self._scroll),
            self._tool("back", "返回上一页。", _obj({}, []), lambda _: Plan("返回上一页", self.browser.back)),
            self._tool("press", "在当前聚焦的元素上按键，例如 Enter、Tab、Escape、ArrowDown、Ctrl+A。",
                       _obj({"key": _str("按键或组合键")}, ["key"]), self._press),
            self._tool("tabs", "列出、切换或关闭这个会话自己的标签页。",
                       _obj({"action": {"type": "string", "enum": ["list", "switch", "close"]},
                             "tab": _int("要切换或关掉的标签页编号")}, []), self._tabs),
            self._tool("upload", "给文件输入框选工作区里的文件。密钥文件不能上传。",
                       _obj({"ref": REF, "path": _str("工作区中的文件路径")}, ["ref", "path"]), self._upload),
            self._tool("screenshot", "截当前页面或指定元素。图片显示在任务页，返回截图位置和页面概况。",
                       _obj({"ref": REF}, []), self._screenshot),
        ]

    @staticmethod
    def _tool(name: str, description: str, params: Args, prepare: Callable[[Args], Plan]) -> CodingTool:
        # 运行时在权限判断、确认和执行之间用同一份计划。浏览器工具串行，快照变化后不会换一个目标执行。
        plan: Plan | None = None
        problem = ""

        def kind(args: Args) -> str:
            nonlocal plan, problem
            plan, problem = None, ""
            try:
                plan = prepare(args)
            except (BrowserError, WorkspaceError, OSError, ValueError) as err:
                problem = str(err)
            return plan.kind if plan else "browser_read"

        async def run(_: Args) -> Outcome:
            nonlocal plan
            if plan is None:
                return Outcome(problem or "没有准备好这个浏览器操作，重新调用工具。", ok=False)
            try:
                async with asyncio.timeout(TOOL_TIMEOUT):
                    result = await plan.run()
                return result if isinstance(result, Outcome) else Outcome(result)
            except TimeoutError:
                return Outcome("网页操作超时了。先用 browser_read 核对页面和已完成的操作，再决定下一步。", ok=False)
            except (BrowserError, WorkspaceError, OSError, ValueError) as err:
                return Outcome(str(err), ok=False)
            finally:
                plan = None

        return CodingTool(
            f"browser_{name}", description, params, run, kind,
            lambda _: plan.action if plan else "检查网页操作",
            lambda _: plan.detail if plan else "",
            permission_key=lambda _: plan.key if plan else "",
        )

    def _open(self, args: Args) -> Plan:
        url = _string(args, "url", MAX_URL).strip()
        port = localhost_port(url)
        if port is None:
            return Plan(f"打开 {short_url(url)}", lambda: self.browser.open(url))
        parts = urlsplit(url)
        self.browser.check_port(port, require_grant=False)

        async def open_local() -> str:
            self.browser.allow_port(port)
            return await self.browser.open(url)

        kind = "browser_read" if self.browser.port_allowed(port) else "browser_local"
        origin = f"{parts.scheme}://{parts.hostname if parts.hostname != '::1' else '[::1]'}:{port}"
        return Plan(f"打开本机的 {origin}", open_local, kind, f"本机端口：{port}\n网址：{url}", str(port))

    def _page_action(self, action: str, execute: Action, *, detail: str = "", safe_get: bool = False) -> Plan:
        title, url = self.browser.page_info
        if not url:
            raise BrowserError("先用 browser_open 打开网页，再用 browser_read 看当前页面。")
        if localhost_port(url) is not None:
            kind = "browser_interact"
        else:
            kind = "browser_get" if safe_get else "browser_external"

        async def guarded() -> str | Outcome:
            if self.browser.page_info != (title, url):
                raise BrowserError("网页已经变了，先用 browser_read 看一下再操作。")
            return await execute()

        return Plan(action, guarded, kind, f"{detail or action}\n页面：{title[:180] or '（无标题）'}（{short_url(url)}）")

    def _element(self, args: Args) -> Element:
        ref = _number(args, "ref")
        element = self.browser.element(ref)
        if element is None:
            raise BrowserError(f"没有编号 {ref} 的元素，先用 browser_read 看一下现在的页面。")
        if element.sensitive:
            raise BrowserError(SENSITIVE)
        return element

    def _click(self, args: Args) -> Plan:
        el = self._element(args)
        if el.kind == "file":
            raise BrowserError("文件输入框用 browser_upload 选文件。")
        if el.download:
            raise BrowserError("浏览器工具不能下载文件。")
        plan = click_plan(el)
        lines = [f"点击：{el.label}"]
        if el.submits and el.action:
            lines.append(f"提交表单：{(el.method or 'post').upper()} {short_url(el.action)}")
        return self._page_action(f"点击{el.label}", lambda: self.browser.click(el.ref, approved=True),
                                 detail="\n".join(lines), safe_get=plan in ("navigate", "get_submit"))

    def _type(self, args: Args) -> Plan:
        el = self._element(args)
        if el.kind != "input":
            raise BrowserError(f"{el.label}不是输入框。")
        text = _string(args, "text", MAX_INPUT, empty=True)
        submit = args.get("submit", False)
        if not isinstance(submit, bool):
            raise BrowserError("submit 要填 true 或 false。")
        lines = [f"在{el.label}里填：{text[:200]}{'…' if len(text) > 200 else ''}"]
        if submit:
            lines.append(f"提交表单：{(el.method or 'post').upper()} {short_url(el.action)}" if el.action else "填完按回车")
        return self._page_action(f"填写{el.label}", lambda: self.browser.type(el.ref, text, submit, approved=True),
                                 detail="\n".join(lines), safe_get=submit and el.get_form)

    def _select(self, args: Args) -> Plan:
        el = self._element(args)
        option = _string(args, "option", MAX_INPUT)
        return self._page_action(f"在{el.label}里选「{option[:180]}」", lambda: self.browser.select(el.ref, option))

    def _scroll(self, args: Args) -> Plan:
        direction = args.get("direction", "down")
        if direction not in ("up", "down"):
            raise BrowserError("翻页方向要填 up 或 down。")
        return Plan("向上翻页" if direction == "up" else "向下翻页", lambda: self.browser.scroll(direction == "down"))

    def _press(self, args: Args) -> Plan:
        key = _string(args, "key", 64)
        _, url = self.browser.page_info
        return self._page_action(f"在当前聚焦的元素上按 {key}", lambda: self.browser.press(key, expected_url=url))

    def _tabs(self, args: Args) -> Plan:
        action = args.get("action", "list")
        if action == "list":
            return Plan("列出这个会话的标签页", self.browser.tabs)
        if action not in ("switch", "close"):
            raise BrowserError("标签页操作要填 list、switch 或 close。")
        tab = _number(args, "tab")
        label = "切换到" if action == "switch" else "关掉"
        return Plan(f"{label}这个会话的标签页 {tab}", lambda: self.browser.tabs(action, tab), "browser_get")

    def _upload_path(self, raw: str) -> Path:
        # 也检查原路径，避免把名为 .env 的链接当作普通文件。拒绝 .git 和 Windows 备用数据流。
        path = self.workspace.resolve(raw, for_write=True)
        if self.workspace.is_secret(Path(raw)) or self.workspace.is_secret(path):
            raise BrowserError("不能把可能含有密码或密钥的文件选给网页。")
        if not path.is_file():
            raise BrowserError("要选工作区里已有的文件。")
        if path.stat().st_nlink > 1:
            raise BrowserError("不能上传硬链接。换一个工作区里的普通文件。")
        return path

    def _upload(self, args: Args) -> Plan:
        el = self._element(args)
        if el.kind != "file":
            raise BrowserError(f"{el.label}不是文件输入框。")
        raw = _string(args, "path", MAX_INPUT)
        path = self._upload_path(raw)

        async def upload() -> str:
            if self._upload_path(raw) != path:
                raise BrowserError("文件路径已经变了，先重新核对再上传。")
            return await self.browser.upload(el.ref, path)

        return self._page_action(f"在{el.label}里选文件 {self.workspace.rel(path)}", upload)

    def _screenshot(self, args: Args) -> Plan:
        ref = self._element(args).ref if "ref" in args else None

        async def screenshot() -> Outcome:
            data, overview = await self.browser.screenshot(ref)
            if not data.startswith(PNG) or len(data) > MAX_SHOT:
                raise BrowserError("截图不可用或超过 16 MiB，试试只截一个元素。")
            self.folder.mkdir(exist_ok=True)
            if self.folder.is_symlink() or self.folder.is_junction():
                raise BrowserError("会话的截图目录不能是链接。")
            if self.folder.resolve() != self.folder.parent.resolve() / self.folder.name:
                raise BrowserError("会话的截图目录不在原处。")
            name = uuid4().hex + ".png"
            path = self.folder / name
            with path.open("xb") as file:
                file.write(data)
            return Outcome(f"截图存在 {path}。\n{overview}", extra={"screenshot": name})

        return Plan("截当前页面" if ref is None else f"截编号 {ref} 的元素", screenshot)


def _number(args: Args, key: str) -> int:
    value = args.get(key)
    if type(value) is not int or value < 1:
        raise BrowserError(f"{key} 要填正整数。")
    return value


def _string(args: Args, key: str, limit: int, *, empty: bool = False) -> str:
    value = args.get(key)
    if not isinstance(value, str) or (not empty and not value.strip()) or len(value) > limit:
        raise BrowserError(f"{key} 要填长度合适的文字（最多 {limit} 字）。")
    return value
