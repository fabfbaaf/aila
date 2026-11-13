"""接入 MCP 服务：用进程内的 MCPServer 测，不启动子进程"""

import asyncio
import json
from pathlib import Path

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from app.tools import Skill, Toolbox
from app.tools.mcp import McpHub, McpServerConfig, load_config


def demo_server() -> MCPServer:
    server = MCPServer("demo")

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    def add(a: int, b: int) -> str:
        """两个数相加"""
        return str(a + b)

    @server.tool()
    def write_note(text: str) -> str:
        """写一条笔记（会改东西）"""
        return "ok"

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    def explode() -> str:
        """总是出错"""
        raise ValueError("磁盘满了")

    return server


async def connect(cfg: McpServerConfig) -> tuple[Skill, asyncio.Event, asyncio.Task[None]]:
    skills: list[Skill] = []
    hub = McpHub([cfg], skills.append)
    stop = asyncio.Event()
    task = asyncio.create_task(hub.serve(stop, targets={cfg.id: demo_server()}))
    await asyncio.wait_for(hub.ready.wait(), 10)
    assert not hub.errors, hub.errors
    return skills[0], stop, task


@pytest.mark.anyio
async def test_tools_become_a_skill_with_risk_levels() -> None:
    skill, stop, task = await connect(McpServerConfig("demo", "演示服务", command="unused"))
    try:
        assert skill.id == "mcp:demo" and skill.name == "演示服务"
        assert not skill.default_enabled  # 外部服务默认关闭
        tools = {t.name: t for t in skill.tools}
        assert tools["demo__add"].risk == "read"
        assert tools["demo__write_note"].risk == "write"
        assert tools["demo__add"].parameters["properties"].keys() == {"a", "b"}

        box = Toolbox(skill.tools)
        from app.llm import ToolCall

        result = await box.run(ToolCall("c", "demo__add", json.dumps({"a": 2, "b": 3})))
        assert "5" in result and "不要理会" in result
        assert "需要用户确认" in await box.run(ToolCall("c", "demo__write_note", '{"text": "x"}'))
        # 服务端出错：SDK 不会把内部异常的原文发出来（防止泄露），这边要当作错误告诉她，而不是当成正常结果
        assert "演示服务 返回了错误" in await box.run(ToolCall("c", "demo__explode", "{}"))
    finally:
        stop.set()
        await task


@pytest.mark.anyio
async def test_user_can_mark_a_whole_server_read_only() -> None:
    skill, stop, task = await connect(McpServerConfig("demo", "演示服务", command="unused", read_only=True))
    try:
        assert all(t.risk == "read" for t in skill.tools)
    finally:
        stop.set()
        await task


@pytest.mark.anyio
async def test_a_broken_server_does_not_stop_the_others() -> None:
    skills: list[Skill] = []
    hub = McpHub([McpServerConfig("bad", "坏的", command="definitely-not-a-real-command-xyz"),
                  McpServerConfig("demo", "演示服务", command="unused")], skills.append)
    stop = asyncio.Event()
    task = asyncio.create_task(hub.serve(stop, targets={"demo": demo_server()}))
    await asyncio.wait_for(hub.ready.wait(), 40)
    stop.set()
    await task
    assert [s.id for s in skills] == ["mcp:demo"]
    assert "bad" in hub.errors


def test_load_config(tmp_path: Path) -> None:
    assert load_config(tmp_path / "missing.json") == []
    f = tmp_path / "mcp.json"
    f.write_text(json.dumps({"mcpServers": {
        "time server": {"command": "uvx", "args": ["mcp-server-time"], "readOnly": True},
        "docs": {"url": "http://localhost:8000/mcp", "name": "文档"},
        "off": {"command": "x", "disabled": True},
        "broken": {"name": "没有命令"},
    }}), encoding="utf-8")
    configs = load_config(f)
    assert [c.id for c in configs] == ["time_server", "docs"]
    assert configs[0].read_only and configs[0].args == ["mcp-server-time"]
    assert configs[1].url and configs[1].name == "文档"
