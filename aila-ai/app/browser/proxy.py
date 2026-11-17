"""编程浏览器的 SOCKS5 出口。Playwright 的路由只检查重定向第一跳，
因此也用同一套地址规则检查 Chromium 每次实际连接，并连接到检查过的 IP。
"""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable

Addresses = Callable[[str, int], Awaitable[list[str]]]
OK = b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00"
DENIED = b"\x05\x02\x00\x01\x00\x00\x00\x00\x00\x00"
CONNECT_TIMEOUT = 10.0


class BrowserProxy:
    def __init__(self, addresses: Addresses) -> None:
        self._addresses = addresses
        self._server: socket.socket | None = None
        self._accepting: asyncio.Task[None] | None = None
        self._connections: set[asyncio.Task[None]] = set()

    async def start(self) -> str:
        server = socket.socket()
        try:
            server.setblocking(False)
            server.bind(("127.0.0.1", 0))
            server.listen()
        except OSError:
            server.close()
            raise
        self._server = server
        self._accepting = asyncio.create_task(self._accept(server))
        return f"socks5://127.0.0.1:{server.getsockname()[1]}"

    async def _accept(self, server: socket.socket) -> None:
        loop = asyncio.get_running_loop()
        while True:
            client, _ = await loop.sock_accept(server)
            task = asyncio.create_task(self._handle(client))
            self._connections.add(task)
            task.add_done_callback(self._connections.discard)

    async def close(self) -> None:
        if self._accepting is not None:
            self._accepting.cancel()
            await asyncio.gather(self._accepting, return_exceptions=True)
            self._accepting = None
        if self._server is not None:
            self._server.close()
            self._server = None
        connections = list(self._connections)
        for task in connections:
            task.cancel()
        await asyncio.gather(*connections, return_exceptions=True)

    async def _handle(self, client: socket.socket) -> None:
        loop = asyncio.get_running_loop()
        remote: socket.socket | None = None
        with client:
            try:
                async with asyncio.timeout(CONNECT_TIMEOUT):
                    version, count = await _read(client, 2)
                    if version != 5 or 0 not in await _read(client, count):
                        return
                    await loop.sock_sendall(client, b"\x05\x00")
                    host, port = await _destination(client)
                    try:
                        remote = await self._connect(host, port)
                    except OSError:
                        await loop.sock_sendall(client, DENIED)
                        return
                    await loop.sock_sendall(client, OK)
                await _relay(client, remote)
            except (OSError, ValueError):
                pass  # 浏览器关闭了连接、目标不可用或收到无效 SOCKS 请求
            finally:
                if remote is not None:
                    remote.close()

    async def _connect(self, host: str, port: int) -> socket.socket:
        loop = asyncio.get_running_loop()
        for address in await self._addresses(host, port):
            remote = socket.socket(socket.AF_INET6 if ":" in address else socket.AF_INET)
            remote.setblocking(False)
            try:
                await loop.sock_connect(remote, (address, port))
                return remote
            except OSError:
                remote.close()
            except BaseException:
                remote.close()
                raise
        raise OSError("浏览器无法连接目标地址")


async def _read(client: socket.socket, size: int) -> bytes:
    loop = asyncio.get_running_loop()
    data = bytearray()
    while len(data) < size:
        chunk = await loop.sock_recv(client, size - len(data))
        if not chunk:
            raise OSError("SOCKS 客户端已断开")
        data.extend(chunk)
    return bytes(data)


async def _destination(client: socket.socket) -> tuple[str, int]:
    version, command, reserved, kind = await _read(client, 4)
    if (version, command, reserved) != (5, 1, 0):
        raise ValueError("只支持 SOCKS5 TCP 连接")
    if kind == 1:
        host = str(ipaddress.IPv4Address(await _read(client, 4)))
    elif kind == 4:
        host = str(ipaddress.IPv6Address(await _read(client, 16)))
    elif kind == 3:
        size = (await _read(client, 1))[0]
        host = (await _read(client, size)).decode("ascii")
    else:
        raise ValueError("无效的 SOCKS5 地址类型")
    return host, int.from_bytes(await _read(client, 2), "big")


async def _copy(source: socket.socket, target: socket.socket) -> None:
    loop = asyncio.get_running_loop()
    while chunk := await loop.sock_recv(source, 65536):
        await loop.sock_sendall(target, chunk)


async def _relay(client: socket.socket, remote: socket.socket) -> None:
    transfers = [asyncio.create_task(_copy(client, remote)), asyncio.create_task(_copy(remote, client))]
    try:
        await asyncio.wait(transfers, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for transfer in transfers:
            transfer.cancel()
        await asyncio.gather(*transfers, return_exceptions=True)
