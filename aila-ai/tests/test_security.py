"""只接受本机的请求：Origin 不认 null（任何网站都能用 sandbox iframe 发出 null），Host 挡 DNS 重绑定"""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app
from app.security import is_local, is_local_host


def test_origin_rules() -> None:
    for ok in (None, "http://localhost:5173", "http://127.0.0.1:5180", "https://[::1]:8443", "file://"):
        assert is_local(ok), ok
    for bad in ("null", "https://evil.example", "http://localhost.evil.example", "http://127.0.0.1.evil.example:8765",
                "file://evil", "http://192.168.1.2:8765"):
        assert not is_local(bad), bad


def test_host_rules() -> None:
    for ok in ("127.0.0.1:8765", "localhost:5180", "LOCALHOST", "[::1]:8765"):
        assert is_local_host(ok), ok
    for bad in (None, "", "evil.example:8765", "127.0.0.1.evil.example", "192.168.1.2:8765", "localhost.evil.example:8765"):
        assert not is_local_host(bad), bad


def test_null_origin_and_rebinding_are_refused() -> None:
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    assert client.get("/health").status_code == 200
    # sandbox iframe 发出的 Origin: null，想加一个会跑命令的 MCP 服务
    res = client.post("/agent/mcp/preview", headers={"origin": "null"}, json={"json": '{"x": {"command": "calc"}}'})
    assert res.status_code == 403
    # DNS 重绑定：Host 是恶意域名，同源 GET 不带 Origin
    assert client.get("/agent/sessions", headers={"host": "evil.example:8765"}).status_code == 403
    assert client.get("/settings", headers={"host": "evil.example:8765"}).status_code == 403


def test_websocket_checks_host_and_origin() -> None:
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    with pytest.raises(WebSocketDisconnect) as err:
        with client.websocket_connect("/voice", headers={"host": "evil.example:8765"}):
            pass
    assert err.value.code == 1008
    with pytest.raises(WebSocketDisconnect) as err:
        with client.websocket_connect("/voice", headers={"origin": "null"}):
            pass
    assert err.value.code == 1008


# ---------- 编程 Agent 的权限：「查看类」命令不能写文件、不能执行别的命令 ----------


@pytest.mark.parametrize("command", [
    "git diff --output=src/app.py",  # --output 写到任意路径
    "git log -p --output ../x.txt",
    "git show HEAD --output=a",
    "git diff --ext-diff",  # 跑 git 配置里的外部程序
    "git log -p --textconv",
    "echo @(Invoke-WebRequest http://x -OutFile y)",  # PowerShell 的 @( ) 和 ( ) 会先执行里面的命令
    "Get-Content (iwr http://x).Content",
])
def test_looks_read_only_but_is_not(command: str, tmp_path) -> None:
    from app.runtime.permissions import Policy, Rules, read_only_command

    assert not read_only_command(command)
    assert Policy(tmp_path, Rules(tmp_path / "r.json"), "read_only").decide("command", command).verdict == "deny"
    assert Policy(tmp_path, Rules(tmp_path / "r.json"), "ask").decide("command", command).verdict == "ask"


@pytest.mark.parametrize("command", [
    "cat ~/.ssh/config", "cat ~/.npmrc", "cat .pypirc", "type _netrc", "cat ~/.aws/config", "cat ~/.kube/config",
    "type C:/Users/me/.docker/config.json", "cat release.keystore",
])
def test_more_credential_files_need_asking(command: str, tmp_path) -> None:
    from app.runtime.permissions import Policy, Rules

    decision = Policy(tmp_path, Rules(tmp_path / "r.json"), "auto").decide("command", command)
    assert decision.verdict == "ask" and not decision.can_remember


def test_credential_files_by_path(tmp_path) -> None:
    from pathlib import Path

    from app.runtime.workspace import Workspace

    for secret in ("proj/.npmrc", "C:/Users/me/.ssh/known_hosts", "/home/me/.aws/credentials", "app/release.jks"):
        assert Workspace.is_secret(Path(secret)), secret
    for plain in ("proj/.docker/Dockerfile", "proj/.env.example", "src/ssh_client.py", "docs/npm.md"):
        assert not Workspace.is_secret(Path(plain)), plain


def test_format_is_only_dangerous_for_disks(tmp_path) -> None:
    from app.runtime.permissions import Policy, Rules, dangerous

    assert dangerous("format D:") and dangerous("format.com c: /q")
    assert dangerous("git log --format=%h") is None and dangerous("dotnet format") is None
    assert Policy(tmp_path, Rules(tmp_path / "r.json"), "read_only").decide("command", "git log --format=%h").verdict == "allow"


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows 的设备名")
def test_windows_device_names_are_not_files(tmp_path) -> None:
    from app.runtime.workspace import Workspace, WorkspaceError

    ws = Workspace(tmp_path)
    for name in ("CON", "nul", "COM1.txt", "sub/LPT1"):
        with pytest.raises(WorkspaceError, match="设备名"):
            ws.resolve(name)
    assert ws.resolve("console.txt") == tmp_path.resolve() / "console.txt"
