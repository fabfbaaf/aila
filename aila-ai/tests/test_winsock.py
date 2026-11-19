"""Windows 上带超时的 socketpair（app/winsock.py）"""

import asyncio
import socket
import sys

import pytest

from app import winsock


def test_pair_is_connected_both_ways() -> None:
    a, b = winsock.socketpair()
    try:
        a.sendall(b"ping")
        assert b.recv(4) == b"ping"
        b.sendall(b"pong")
        assert a.recv(4) == b"pong"
        assert a.gettimeout() is None and b.gettimeout() is None  # 和原来的一样是阻塞的
    finally:
        a.close()
        b.close()


def test_accept_that_times_out_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    real = winsock._once

    def flaky(*args):
        calls.append(args)
        if len(calls) == 1:
            raise TimeoutError("连接没到")
        return real(*args)

    monkeypatch.setattr(winsock, "_once", flaky)
    a, b = winsock.socketpair()
    a.close()
    b.close()
    assert len(calls) == 2

    monkeypatch.setattr(winsock, "_once", lambda *a: (_ for _ in ()).throw(TimeoutError("一直不到")))
    with pytest.raises(TimeoutError):
        winsock.socketpair()


@pytest.mark.skipif(sys.platform != "win32", reason="只在 Windows 上替换")
def test_installed_for_asyncio() -> None:
    # conftest 已经装上了：asyncio 建事件循环用的就是它
    assert socket.socketpair is winsock.socketpair
    assert asyncio.run(asyncio.sleep(0, "ok")) == "ok"
