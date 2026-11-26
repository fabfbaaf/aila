"""「安卓手机」技能包：她操作你的安卓手机（看屏幕、点、打字、滑、按键、打开 App），规则见 app/android。

只有在 设置 → 手机 里打开「让她操作安卓手机」、选好手机、找得到 ADB 时才把这些工具给她。
会产生后果的点击（发送、支付、删除、确认、安装、允许……，以及支付和银行 App 里的任何点击）先问你，
问的时候给你看的是代码从屏幕清单里拿的按钮文字和所在 App；同意以后执行前再核对一遍屏幕。
"""

from collections.abc import Awaitable, Callable
from typing import Any

from ..android import APPS, AdbError, AndroidSession
from . import APPROVED, UNTRUSTED_NOTE, Skill, Tool, ToolError

REF = {"type": "integer", "description": "phone_read 列出的编号"}


def android_skill(session: Callable[[], AndroidSession | None], available: Callable[[], bool]) -> Skill:
    def phone() -> AndroidSession:
        s = session()
        if s is None:
            raise ToolError("手机没连上：在电脑上 设置 → 手机 里看看「让她操作安卓手机」开着没、手机选好没。")
        return s

    def wrap(fn: Callable[[AndroidSession, dict[str, Any]], Awaitable[str]]) -> Callable[[dict[str, Any]], Awaitable[str]]:
        async def run(args: dict[str, Any]) -> str:
            try:
                return f"{UNTRUSTED_NOTE}\n{await fn(phone(), args)}"
            except AdbError as err:
                raise ToolError(str(err)) from err

        return run

    def ref(args: dict[str, Any]) -> int:
        try:
            return int(args.get("ref", 0))
        except (TypeError, ValueError):
            return 0

    def tap_confirm(args: dict[str, Any]) -> bool:
        s = session()
        return s is None or s.needs_confirm(ref(args))

    def tap_label(args: dict[str, Any]) -> str:
        s = session()
        el = s.element(ref(args)) if s else None
        name = (el.label if el else "") or "这个按钮"
        return name if len(name) <= 20 else name[:20] + "…"

    def tap_detail(args: dict[str, Any]) -> str:
        s = session()
        el = s.element(ref(args)) if s else None
        app = s.screen.package if s and s.screen else ""
        return f"在手机上点：{el.label if el else '（编号对不上）'}\n所在 App：{app or '不知道'}"

    async def read(s: AndroidSession, _: dict[str, Any]) -> str:
        return await s.read()

    async def tap(s: AndroidSession, args: dict[str, Any]) -> str:
        return await s.tap(ref(args), approved=APPROVED.get())

    async def type_(s: AndroidSession, args: dict[str, Any]) -> str:
        return await s.type(ref(args), str(args.get("text", "")), bool(args.get("enter")))

    async def swipe(s: AndroidSession, args: dict[str, Any]) -> str:
        return await s.swipe(str(args.get("direction", "down")))

    async def key(s: AndroidSession, args: dict[str, Any]) -> str:
        return await s.key(str(args.get("key", "")))

    async def open_app(s: AndroidSession, args: dict[str, Any]) -> str:
        return await s.open_app(str(args.get("app", "")))

    obj = "object"
    return Skill("android", "安卓手机", "她操作你的安卓手机：看屏幕、点、打字、打开 App（会产生后果的先问你）", [
        Tool("phone_read", "看一下用户的安卓手机现在的屏幕：在哪个 App、能点的按钮和输入框（带编号）、屏幕上的文字。操作之前先看。",
             {"type": obj, "properties": {}}, wrap(read), lambda _: "正在看手机屏幕"),
        Tool("phone_tap", "点手机屏幕上的元素（按编号）。点普通的（打开页面、切换标签）直接点；发送、支付、删除、确认这类会先问用户。",
             {"type": obj, "properties": {"ref": REF}, "required": ["ref"]},
             wrap(tap), lambda a: f"正在点手机上的「{tap_label(a)}」",
             risk="mixed", needs_confirm=tap_confirm, action=lambda a: f"在手机上点「{tap_label(a)}」", detail=tap_detail),
        Tool("phone_type", "往手机的输入框里打字（按编号），enter=true 时打完按回车。密码、验证码、卡号不能填，要用户自己来。",
             {"type": obj, "properties": {
                 "ref": REF,
                 "text": {"type": "string", "description": "要输入的文字"},
                 "enter": {"type": "boolean", "description": "打完要不要按回车，默认不按"},
             }, "required": ["ref", "text"]},
             wrap(type_), lambda _: "正在手机上打字"),
        Tool("phone_swipe", "在手机上翻：down 看下面的内容，up 看上面的，left / right 左右翻。",
             {"type": obj, "properties": {"direction": {"type": "string", "enum": ["down", "up", "left", "right"]}}},
             wrap(swipe), lambda _: "正在翻手机屏幕"),
        Tool("phone_key", "按手机的键：back 返回、home 回桌面、recents 最近任务、enter 回车。",
             {"type": obj, "properties": {"key": {"type": "string", "enum": ["back", "home", "recents", "enter"]}}, "required": ["key"]},
             wrap(key), lambda a: f"正在按手机的{ {'back': '返回', 'home': '主屏幕', 'recents': '最近任务', 'enter': '回车'}.get(str(a.get('key')), '')}键"),
        Tool("phone_open_app", f"在手机上打开一个 App。常用的直接写名字（{'、'.join(list(APPS)[:10])}……），别的写包名。",
             {"type": obj, "properties": {"app": {"type": "string", "description": "App 名字或包名"}}, "required": ["app"]},
             wrap(open_app), lambda a: f"正在打开手机上的 {a.get('app', '')}"),
    ], available=available)
