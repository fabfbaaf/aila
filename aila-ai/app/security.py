"""只接受本机的请求。

服务只监听 127.0.0.1，别的电脑连不上；但你浏览器里打开的任何网页都能向 127.0.0.1 发请求。
现在能通过接口改配置（包括大模型密钥和地址），所以凡是带了 Origin 头、又不是本机页面的请求一律拒绝。
桌面端主进程（Node）发的请求不带 Origin；打包后的页面是 file://；开发时是 http://localhost:端口。
"""

import re

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

LOCAL_ORIGIN = re.compile(r"^(https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?|file://|null)$")


def is_local(origin: str | None) -> bool:
    return origin is None or bool(LOCAL_ORIGIN.match(origin))


class LocalOnly:
    """HTTP 请求的 Origin 检查（WebSocket 在 /voice 里自己检查，那里要用 1008 关闭码）"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            origin = Request(scope).headers.get("origin")
            if not is_local(origin):
                response: Response = JSONResponse({"detail": "只接受本机的请求"}, status_code=403)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
