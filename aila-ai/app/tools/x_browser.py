"""把 X 的首页、提及页和带草稿的编辑页交给用户，沿用艾拉浏览器的登录状态。"""

import re
from typing import Any
from urllib.parse import urlencode, urlsplit

from ..browser import BrowserError, BrowserSession
from . import Skill, Tool, ToolError

PAGES = {"home": "https://x.com/home", "mentions": "https://x.com/notifications/mentions"}
INTENT = "https://x.com/intent/tweet"
POST_ID = r"[1-9][0-9]{0,19}"
MAX_TEXT = 2000


def _post_id(target: Any) -> str:
    """从帖子链接只取数字编号，分享链接中的追踪参数不会带进编辑页。"""
    if isinstance(target, str):
        if re.fullmatch(POST_ID, target):
            return target
        if len(target) <= 2048 and not re.search(r"[\s\\\x00-\x1f\x7f]", target):
            try:
                url = urlsplit(target)
            except ValueError:
                pass
            else:
                if url.scheme == "https" and url.netloc.lower() in (
                    "x.com", "www.x.com", "twitter.com", "www.twitter.com",
                ):
                    path = re.fullmatch(rf"/(?:[A-Za-z0-9_]{{1,15}}/status|i/web/status)/({POST_ID})/?", url.path)
                    if path:
                        return path[1]
    raise ValueError("回复目标要用原帖的 X 链接，或它的数字编号。")


def _destination(args: dict[str, Any]) -> tuple[str, str]:
    """用同一份校验后的参数生成实际网址和确认卡片。"""
    page = args.get("page")
    if not isinstance(page, str) or page not in (*PAGES, "post", "reply"):
        raise ValueError("选择要打开的 X 页面：home、mentions、post 或 reply。")
    if args.keys() - {"page", "text", "target"}:
        raise ValueError("X 页面只接受 page、text 和 target 参数。")
    if page in PAGES:
        if "target" in args or "text" in args:
            raise ValueError("首页和提及页只需要 page 参数。")
        return PAGES[page], PAGES[page]

    text = args.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
        raise ValueError(f"要预填的草稿应有 1 到 {MAX_TEXT} 个字符。")
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]", text):
        raise ValueError("草稿里有无效字符。")
    params = {"text": text}
    detail = "X 发帖页（x.com）"
    if page == "reply":
        post_id = _post_id(args.get("target"))
        params["in_reply_to"] = post_id
        detail = f"回复原帖 https://x.com/i/web/status/{post_id}"
    elif "target" in args:
        raise ValueError("只有回复原帖时才需要 target。")
    return f"{INTENT}?{urlencode(params)}", f"{detail}\n打开时会把以下草稿带到 X 编辑页：\n{text}"


def x_browser_skill(session: BrowserSession) -> Skill:
    def validate(args: dict[str, Any]) -> str | None:
        try:
            _destination(args)
        except ValueError as err:
            return str(err)
        return None

    async def run(args: dict[str, Any]) -> str:
        url, _ = _destination(args)
        try:
            await session.open_for_user(url)
        except BrowserError as err:
            raise ToolError(str(err)) from err
        if args["page"] in PAGES:
            return "X 页面已打开，可以在艾拉的浏览器里查看。想让我总结时，把相关文字贴到对话里。"
        return "已打开 X 编辑页。你可以核对账号、修改草稿，再亲自点击发布。"

    tool = Tool(
        "x_open",
        "用户想在艾拉的浏览器里亲自查看 X 首页、提及，或者把定好的草稿带到发帖或回复页面时用。"
        "只要草稿时直接在对话里写。要总结动态时请用户贴出内容。打开后由用户登录、核对账号和发布。",
        {"type": "object", "additionalProperties": False, "properties": {
            "page": {"type": "string", "enum": [*PAGES, "post", "reply"],
                     "description": "home 首页，mentions 提及，post 单帖，reply 回复。"},
            "text": {"type": "string", "minLength": 1, "maxLength": MAX_TEXT,
                     "description": "post 和 reply 需要预填的完整草稿。"},
            "target": {"type": "string", "description": "仅 reply 使用，原帖的 X 链接或数字编号。"},
        }, "required": ["page"]},
        run, lambda _: "正在打开 X", risk="write", action="在艾拉的浏览器里打开 X 页面",
        detail=lambda args: _destination(args)[1], validate=validate,
    )
    return Skill("x_browser", "X 浏览器助手", "查看 X 首页和提及，把帖子或回复草稿带到浏览器里亲自发布。",
                 [tool], default_enabled=False)
