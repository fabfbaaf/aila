"""MCP：客户端协议、服务管理、权限、接到编程 Agent 上（用 fake_mcp_server.py 这个真的子进程测）"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

from app.runtime.mcp import (
    CALL_TIMEOUT,
    McpError,
    McpManager,
    ServerConfig,
    StdioClient,
    bridge_tools,
    parse_import,
    render_result,
    tool_name,
)
from app.runtime.permissions import Policy, Rules
from app.runtime.runner import Runtime

from .test_runtime import SHELL, Scripted, call

FAKE = str(Path(__file__).with_name("fake_mcp_server.py"))


def fake_config(name: str = "fake", *extra: str, **kw) -> ServerConfig:
    return ServerConfig(name=name, command=sys.executable, args=[FAKE, *extra], **kw)


def write_config(path: Path, servers: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


# ---------- 配置 ----------


def test_parse_config_and_import_formats() -> None:
    c = ServerConfig.parse("github", {"command": "npx", "args": ["-y", "@x/server-github"], "env": {"TOKEN": "t"},
                                      "alwaysAllow": ["search", 3], "timeout": 30})
    assert c.args == ["-y", "@x/server-github"] and c.env == {"TOKEN": "t"} and c.always_allow == ["search"] and c.timeout == 30
    assert c.command_line() == "npx -y @x/server-github"
    assert ServerConfig.parse("a", {"command": "x", "timeout": True}).timeout == CALL_TIMEOUT
    assert ServerConfig.parse("a", c.to_raw() | {"command": "npx"}).to_raw() == c.to_raw()
    for name, raw, msg in [("bad name", {"command": "x"}, "服务名"), ("a", [], "对象"), ("a", {}, "command"),
                           ("a", {"command": "x", "args": "y"}, "args"), ("a", {"command": "x", "env": {"K": 1}}, "env")]:
        with pytest.raises(McpError, match=msg):
            ServerConfig.parse(name, raw)
    both = parse_import('{"mcpServers": {"a": {"command": "x"}, "b": {"url": "https://x"}}}')
    assert list(both) == ["a", "b"] and both["b"].url == "https://x"
    assert list(parse_import('{"solo": {"command": "x"}}')) == ["solo"]
    with pytest.raises(McpError, match="JSON"):
        parse_import("{oops")
    with pytest.raises(McpError, match="没找到"):
        parse_import("[]")


def test_tool_names_are_safe_for_the_model() -> None:
    assert tool_name("fake", "echo") == "mcp__fake__echo"
    assert tool_name("fake", "weird name/with.dots") == "mcp__fake__weird_name_with_dots"
    long = tool_name("server", "x" * 100)
    assert len(long) == 64 and long.startswith("mcp__server__x") and long != tool_name("server", "x" * 99)


def test_render_result_kinds_and_limits() -> None:
    assert render_result({"content": [{"type": "text", "text": "好"}], "isError": True}) == ("好", True)
    text, _ = render_result({"content": [{"type": "image", "mimeType": "image/png"}, {"type": "resource", "resource": {"text": "正文"}},
                                         {"type": "resource_link", "uri": "file:///a"}]})
    assert "[图片 image/png" in text and "正文" in text and "file:///a" in text
    assert render_result({"content": [], "structuredContent": {"a": 1}})[0] == '{"a": 1}'
    assert render_result(None)[0] == "（工具没有返回内容）"
    assert "太长了" in render_result({"content": [{"type": "text", "text": "x" * 30000}]})[0]


# ---------- 客户端（真的子进程） ----------


@pytest.mark.anyio
async def test_client_handshake_pagination_calls_and_server_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-should-not-leak")
    client = StdioClient(fake_config(env={"FAKE_TOKEN": "x"}))
    await client.start()
    try:
        assert client.server_info == {"name": "fake", "version": "1.0"}
        tools = await client.list_tools()
        assert [t.name for t in tools] == ["echo", "write_note", "fail", "slow", "env", "weird name/with.dots"]  # 两页
        assert tools[0].read_only and not tools[1].read_only and tools[2].schema["properties"] == {}
        assert await client.call("echo", {"text": "你好"}) == ("你好", False)
        assert await client.call("fail", {}) == ("数据库连不上", True)
        # 艾拉自己的密钥不传给服务；配置里给服务的环境变量传进去
        assert await client.call("env", {}) == ("FAKE_TOKEN", False)
        with pytest.raises(McpError, match="没有 nope"):
            await client.call("nope", {})
        await asyncio.sleep(0.2)
        assert "fake server ready" in client.log  # stderr 留着诊断
    finally:
        await client.close()
    assert not client.alive


@pytest.mark.anyio
async def test_client_timeout_and_crash() -> None:
    client = StdioClient(fake_config(timeout=1))
    await client.start()
    try:
        with pytest.raises(McpError, match="没回应"):
            await client.call("slow", {"seconds": 5})
    finally:
        await client.close()

    crashing = StdioClient(fake_config("crash", "--crash-on-call"))
    await crashing.start()
    with pytest.raises(McpError, match="退出"):
        await crashing.call("echo", {"text": "a"})
    await crashing.close()


@pytest.mark.anyio
async def test_client_missing_command_and_noisy_stdout() -> None:
    with pytest.raises(McpError, match="找不到命令"):
        await StdioClient(ServerConfig(name="x", command="definitely-not-a-command-aila")).start()
    noisy = StdioClient(fake_config("noisy", "--noisy"))
    await noisy.start()  # stdout 上混了日志也能连上
    assert any("by mistake" in line for line in noisy.log)
    await noisy.close()


# ---------- 管理 ----------


@pytest.mark.anyio
async def test_manager_starts_saves_disables_and_removes(tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    write_config(path, {
        "fake": {"command": sys.executable, "args": [FAKE], "env": {"FAKE_TOKEN": "secret-value"}},
        "off": {"command": sys.executable, "args": [FAKE], "disabled": True},
        "remote": {"url": "https://example.com/mcp"},
        "broken": {"command": "definitely-not-a-command-aila"},
    })
    m = McpManager(path)
    await m.wait_ready(30)
    status = {s["name"]: s for s in m.status()}
    assert status["fake"]["status"] == "ready" and len(status["fake"]["tools"]) == 6
    assert status["fake"]["envKeys"] == ["FAKE_TOKEN"] and "secret-value" not in json.dumps(m.status())
    assert status["off"]["status"] == "disabled" and status["remote"]["status"] == "unsupported"
    assert status["broken"]["status"] == "failed" and "找不到命令" in status["broken"]["error"]
    assert [t.full_name for t in m.tools()][:2] == ["mcp__fake__echo", "mcp__fake__write_note"]

    # 「以后都不用问」写进 alwaysAllow
    assert not m.allowed("mcp__fake__write_note")
    m.allow("mcp__fake__write_note")
    assert m.allowed("mcp__fake__write_note") and m.configs()["fake"].always_allow == ["write_note"]
    assert m.forget("fake", "write_note") and not m.allowed("mcp__fake__write_note")

    # 改配置：界面不回传密钥，keep_env 时保留原来的
    await m.save(ServerConfig(name="fake", command=sys.executable, args=[FAKE]), keep_env=True)
    assert m.configs()["fake"].env == {"FAKE_TOKEN": "secret-value"}
    assert {s["name"]: s for s in m.status()}["fake"]["status"] == "ready"

    await m.set_disabled("fake", True)
    assert {s["name"]: s for s in m.status()}["fake"]["status"] == "disabled" and m.tools() == []
    await m.remove("fake")
    assert "fake" not in m.configs()
    with pytest.raises(McpError, match="没有叫"):
        await m.remove("fake")
    await m.stop_all()


# ---------- 权限 ----------


class Allow:
    def __init__(self) -> None:
        self.names: set[str] = set()

    def allowed(self, name: str) -> bool:
        return name in self.names

    def allow(self, name: str) -> None:
        self.names.add(name)


def test_policy_for_mcp_tools(tmp_path: Path) -> None:
    allow = Allow()
    for mode in ("read_only", "ask", "auto"):
        p = Policy(tmp_path, Rules(tmp_path / "r.json"), mode, allow)  # type: ignore[arg-type]
        assert p.decide("mcp_read").verdict == "allow"
        expected = "deny" if mode == "read_only" else "ask"
        d = p.decide("mcp", "mcp__fake__write_note")
        assert d.verdict == expected and (mode == "read_only" or d.can_remember)
    p = Policy(tmp_path, Rules(tmp_path / "r.json"), "auto", allow)
    p.remember("mcp", "mcp__fake__write_note")
    assert p.decide("mcp", "mcp__fake__write_note").verdict == "allow"
    assert p.decide("mcp", "mcp__fake__other").verdict == "ask"
    assert Policy(tmp_path, Rules(tmp_path / "r.json"), "read_only", allow).decide("mcp", "mcp__fake__write_note").verdict == "deny"


# ---------- 接到编程 Agent 上 ----------


@pytest.mark.anyio
async def test_agent_uses_mcp_tools_with_confirmation(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    write_config(tmp_path / "data" / "mcp.json", {"fake": {"command": sys.executable, "args": [FAKE]}})
    model = Scripted([
        [call("mcp__fake__echo", text="查一下")],
        [call("mcp__fake__write_note", text="笔记")],
        [call("mcp__fake__fail")],
        ["好了"],
    ])
    rt = Runtime(tmp_path / "data", lambda: model, shell=SHELL)
    sid = rt.create(str(root), "auto").id
    seen = []
    async for e in rt.run(sid, "用外部工具"):
        seen.append(e)
        if e["type"] == "confirm":
            assert e["kind"] == "mcp" and e["can_remember"]
            if e["tool"] == "mcp__fake__write_note":
                # 给你看的是代码拼的服务名、工具名和参数
                assert "MCP 服务 fake 的工具 write_note" in e["detail"] and '"text": "笔记"' in e["detail"]
            rt.confirms.answer(e["id"], True, remember=e["tool"] == "mcp__fake__write_note")
    tools = [e for e in seen if e["type"] == "tool"]
    assert [(t["name"], t["status"]) for t in tools] == [
        ("mcp__fake__echo", "ok"), ("mcp__fake__write_note", "ok"), ("mcp__fake__fail", "error")]
    assert tools[0]["content"] == "查一下" and tools[0]["mcp"] == {"server": "fake", "tool": "echo"}
    assert [e["tool"] for e in seen if e["type"] == "confirm"] == ["mcp__fake__write_note", "mcp__fake__fail"]  # 只读的不问
    assert rt.mcp.configs()["fake"].always_allow == ["write_note"]
    # 工具列表在一轮开始前对齐：MCP 的放在最后
    names = [t["function"]["name"] for t in model.requests[0][1]]
    assert names[-6:] == [t.full_name for t in rt.mcp.tools()] and names[-7] == "remember"
    await rt.shutdown()
    assert rt.mcp.servers == {}


@pytest.mark.anyio
async def test_tools_stay_put_within_a_turn_and_update_between_turns(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    rt = Runtime(tmp_path / "data", lambda: Scripted([["好"]]), shell=SHELL)
    sid = rt.create(str(root)).id
    [e async for e in rt.run(sid, "一")]
    agent = rt.agent(sid)
    assert not [n for n in agent.tools.tools if n.startswith("mcp__")]
    await rt.mcp.save(fake_config())
    [e async for e in rt.run(sid, "二")]
    assert "mcp__fake__echo" in agent.tools.tools
    assert not agent.tools.replace_prefixed("mcp__", bridge_tools(rt.mcp))  # 没变就不动
    await rt.shutdown()


# ---------- 接口、终端 ----------


@pytest.mark.anyio
async def test_mcp_api_preview_import_toggle_remove(tmp_path: Path) -> None:
    import httpx

    from app.deps import get_runtime
    from app.main import app

    rt = Runtime(tmp_path / "data", lambda: Scripted([["好"]]), shell=SHELL)
    app.dependency_overrides[get_runtime] = lambda: rt
    paste = json.dumps({"mcpServers": {"fake": {"command": sys.executable, "args": [FAKE], "env": {"FAKE_TOKEN": "secret-value"}}}})
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver", timeout=60) as c:
            assert (await c.get("/agent/mcp")).json() == []
            preview = (await c.post("/agent/mcp/preview", json={"json": paste})).json()
            assert [{k: v for k, v in p.items() if k != "command"} for p in preview] == [
                {"name": "fake", "envKeys": ["FAKE_TOKEN"], "exists": False, "supported": True}]
            assert preview[0]["command"].startswith(sys.executable) and "fake_mcp_server.py" in preview[0]["command"]
            assert not (tmp_path / "data" / "mcp.json").exists()  # 预览不保存
            assert (await c.post("/agent/mcp/preview", json={"json": "{oops"})).status_code == 422
            servers = (await c.post("/agent/mcp/import", json={"json": paste})).json()
            assert servers[0]["status"] == "ready" and len(servers[0]["tools"]) == 6
            assert "secret-value" not in json.dumps(servers)
            servers = (await c.put("/agent/mcp/fake/disabled", json={"disabled": True})).json()
            assert servers[0]["status"] == "disabled"
            assert (await c.post("/agent/mcp/nope/restart")).status_code == 404
            assert (await c.request("DELETE", "/agent/mcp/fake/allow", json={"tool": "echo"})).status_code == 404
            assert (await c.delete("/agent/mcp/fake")).json() == []
            assert (await c.get("/agent/mcp", headers={"origin": "https://example.com"})).status_code == 403
    finally:
        app.dependency_overrides.pop(get_runtime, None)
        await rt.shutdown()


@pytest.mark.anyio
async def test_terminal_mcp_command(tmp_path: Path) -> None:
    import io

    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    from rich.console import Console

    from app.cli.app import Terminal
    from app.cli.backend import EmbeddedBackend

    root = tmp_path / "proj"
    root.mkdir()
    rt = Runtime(tmp_path / "data", lambda: Scripted([["好"]]), shell=SHELL)
    backend = EmbeddedBackend(rt)
    console = Console(file=io.StringIO(), record=True, width=200, force_terminal=False, color_system=None)
    try:
        info = await backend.create(str(root), "ask")
        with create_pipe_input() as inp:
            term = Terminal(backend, console, info, inp=inp, output=DummyOutput())
            await term.command("/mcp")
            assert "还没有 MCP 服务" in console.export_text(clear=True)
            write_config(tmp_path / "data" / "mcp.json", {"fake": {"command": sys.executable, "args": [FAKE]}})
            await term.command("/mcp restart fake")
            out = console.export_text(clear=True)
            assert "fake  在运行" in out and "· echo（只读）" in out and "· write_note" in out
            await term.command("/mcp oops")
            assert "用法" in console.export_text(clear=True)
    finally:
        await backend.close()
        await rt.shutdown()


# ---------- 和对话那边共用 mcp.json ----------


@pytest.mark.anyio
async def test_shares_config_with_chat_side(tmp_path: Path) -> None:
    from app.tools.mcp import load_config

    path = tmp_path / "mcp.json"
    # 对话那边例子的写法：servers、显示名 name、整个服务 readOnly、远程 url
    path.write_text(json.dumps({"servers": {
        "fake": {"name": "假服务", "command": sys.executable, "args": [FAKE], "readOnly": True},
        "docs": {"name": "团队文档", "url": "http://localhost:8000/mcp"},
    }, "note": "别的字段也要留着"}, ensure_ascii=False), encoding="utf-8")
    m = McpManager(path)
    await m.wait_ready(30)
    status = {s["name"]: s for s in m.status()}
    assert status["fake"]["label"] == "假服务" and status["fake"]["readOnly"]
    assert all(t.read_only for t in m.tools())  # 整个服务标了只读：工具都算读，不用问
    assert status["docs"]["status"] == "unsupported" and "对话里能用" in status["docs"]["error"]

    # 改了以后照原样写回：还是 servers，name、readOnly、别的字段都在；对话那边读得懂
    m.allow("mcp__fake__write_note")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert set(data) == {"servers", "note"} and data["servers"]["fake"]["name"] == "假服务"
    assert data["servers"]["fake"]["readOnly"] is True and data["servers"]["fake"]["alwaysAllow"] == ["write_note"]
    assert {c.id: c.name for c in load_config(path)} == {"fake": "假服务", "docs": "团队文档"}

    # 新文件按 Claude Desktop 的写法；对话那边也读得懂
    fresh = McpManager(tmp_path / "new.json")
    await fresh.save(fake_config("added"))
    assert list(json.loads((tmp_path / "new.json").read_text(encoding="utf-8"))) == ["mcpServers"]
    assert [c.id for c in load_config(tmp_path / "new.json")] == ["added"]
    await m.stop_all()
    await fresh.stop_all()


def test_runtime_uses_given_config_path(tmp_path: Path) -> None:
    rt = Runtime(tmp_path / "data", lambda: Scripted([["好"]]), shell=SHELL, mcp_config=tmp_path / "shared.json")
    assert rt.mcp.path == tmp_path / "shared.json"
    assert Runtime(tmp_path / "d2", lambda: Scripted([["好"]]), shell=SHELL).mcp.path == tmp_path / "d2" / "mcp.json"
