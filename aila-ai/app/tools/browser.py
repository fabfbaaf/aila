"""「浏览器」技能包：在用户自己的浏览器里打开网页。

和 read_page 不同：read_page 是她自己在后台读，用户看不到；这里是替用户打开，让用户自己看。
打开网页会产生实际效果（链接可能触发下载、退订、点赞这类操作），网址也可能来自被注入的网页内容，
所以每次都要用户确认，确认时显示完整网址。以后的浏览器操作（点按钮、填表）也放在这个技能包里。
"""

import asyncio
import webbrowser
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from . import Skill, Tool, ToolError

MAX_URL_CHARS = 2000


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
        "在用户自己的浏览器里打开一个网页，让用户自己看。用户说「打开……网站 / 链接」时使用；"
        "只是你自己想读内容时用 read_page。",
        {"type": "object", "properties": {"url": {"type": "string", "description": "完整网址，以 http:// 或 https:// 开头"}}, "required": ["url"]},
        run,
        lambda a: f"正在打开 {urlsplit(str(a.get('url', ''))).hostname or '网页'}",
        risk="write",
        action="在浏览器里打开这个网页",
        detail=lambda a: str(a.get("url", "")).strip(),
        validate=check_url,
    )
    return Skill("browser", "浏览器", "在你的浏览器里打开网页（每次都会先问你）", [tool])
