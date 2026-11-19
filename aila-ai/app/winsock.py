"""Windows 上 socket.socketpair 偶尔卡死的兜底。

asyncio 每建一个事件循环都要一对互相连着的 socket（「自唤醒管道」）。Windows 没有原生的 socketpair，
Python 的做法是：在 127.0.0.1 上监听一个随机端口，非阻塞地连过去（不等结果），再阻塞地 accept。
要是这次连接没到（被别的程序拦了、端口一时用光……），accept 就永远等下去，而且没有超时：
测试跑到一半整个卡住（每次卡在不同的测试上，单独跑又都过），服务启动时碰上就一直起不来。
3.12.4 起会检查连进来的是不是自己，但还是没有超时。

这里换成：accept 最多等 5 秒，连进来的不是自己就丢掉接着等，超时就整个重来，最多 3 次。
只在 Windows 上换；测试（conftest.py）和服务入口（__main__.py）各装一次。
"""

import socket
import sys

ACCEPT_TIMEOUT = 5.0
ATTEMPTS = 3

_original = socket.socketpair


def _once(family: int, type_: int, proto: int) -> tuple[socket.socket, socket.socket]:
    host = "::1" if family == socket.AF_INET6 else "127.0.0.1"
    lsock = socket.socket(family, type_, proto)
    try:
        lsock.bind((host, 0))
        lsock.listen()
        lsock.settimeout(ACCEPT_TIMEOUT)
        addr = lsock.getsockname()[:2]
        csock = socket.socket(family, type_, proto)
        try:
            csock.setblocking(False)
            try:
                csock.connect(addr)
            except (BlockingIOError, InterruptedError):
                pass
            csock.setblocking(True)
            while True:
                ssock, peer = lsock.accept()  # 超时抛 TimeoutError，由外面重来
                if peer[:2] == csock.getsockname()[:2]:
                    ssock.settimeout(None)
                    return ssock, csock
                ssock.close()  # 不是自己连的（别的程序碰巧连到这个端口）：丢掉
        except BaseException:
            csock.close()
            raise
    finally:
        lsock.close()


def socketpair(family: int = socket.AF_INET, type: int = socket.SOCK_STREAM, proto: int = 0):  # noqa: A002
    if family not in (socket.AF_INET, socket.AF_INET6) or type != socket.SOCK_STREAM or proto != 0:
        return _original(family, type, proto)
    for attempt in range(ATTEMPTS):
        try:
            return _once(family, type, proto)
        except TimeoutError:
            if attempt == ATTEMPTS - 1:
                raise
    raise AssertionError("不会到这里")


def install() -> None:
    """只在 Windows 上把 socket.socketpair 换成带超时的版本（asyncio 通过 socket.socketpair 调它）"""
    if sys.platform == "win32":
        socket.socketpair = socketpair
