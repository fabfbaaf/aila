"""只接受本机的请求。

服务只监听 127.0.0.1，别的电脑连不上；但你浏览器里打开的任何网页都能向 127.0.0.1 发请求。
接口能改配置（包括大模型密钥和地址）、开编程任务、加 MCP 服务（也就是在这台电脑上跑程序），所以有两道检查：

- Origin：带了 Origin 头、又不是本机页面的请求一律拒绝。桌面端主进程（Node）发的请求不带 Origin；
  打包后的页面连 WebSocket 时是 file://（实测），发 HTTP 时不带；开发时是 http://localhost:端口。
  **不认 null**：任何网站都能用加了 sandbox 的 iframe 发出 Origin: null 的请求，认了就等于谁都能调
- Host：只认 127.0.0.1、localhost、[::1]。挡 DNS 重绑定：恶意域名先解析到自己、再解析到 127.0.0.1，
  它的网页就成了「同源」，Chrome 的同源 GET 又不带 Origin，光看 Origin 挡不住读会话、代码、记忆
"""

import re

from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

LOCAL_ORIGIN = re.compile(r"^(https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?|file://)$")
LOCAL_HOST = re.compile(r"^(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$")
# 测试里的 TestClient 用 testserver 当 Host（conftest 里加进来；正式运行时没有）
EXTRA_HOSTS: set[str] = set()


def is_local(origin: str | None) -> bool:
    return origin is None or bool(LOCAL_ORIGIN.match(origin))


def is_local_host(host: str | None) -> bool:
    if not host:
        return False
    host = host.strip().lower()
    return bool(LOCAL_HOST.match(host)) or host.split(":")[0] in EXTRA_HOSTS


class LocalOnly:
    """HTTP 请求检查 Host 和 Origin；WebSocket 只检查 Host（Origin 在 /voice、/screen 里自己检查，那里要用 1008 关闭码）"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            conn = HTTPConnection(scope)
            if not is_local_host(conn.headers.get("host")):
                await self._deny(scope, receive, send, "只接受发到本机地址的请求")
                return
            if scope["type"] == "http" and not is_local(conn.headers.get("origin")):
                await self._deny(scope, receive, send, "只接受本机的请求")
                return
        await self.app(scope, receive, send)

    async def _deny(self, scope: Scope, receive: Receive, send: Send, detail: str) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        response: Response = JSONResponse({"detail": detail}, status_code=403)
        await response(scope, receive, send)
