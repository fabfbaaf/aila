"""对话的大脑换成本机 Agent（app/brain.py）：用假的 ACP 连接测，对话流程（run_agent）、工具、确认都是真的。"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import acp
import pytest
from acp import schema
from fastapi.testclient import TestClient

from app import deps
from app.agent import run_agent
from app.brain import AgentBrain, BrainError
from app.confirm import Confirmations
from app.llm import ToolCall
from app.main import app

SPEC = {"type": "function", "function": {"name": "set_reminder", "description": "记一个提醒",
                                         "parameters": {"type": "object", "properties": {"what": {"type": "string"}}}}}


class FakeToolbox:
    specs = [SPEC]

    def __init__(self) -> None:
        self.ran: list[tuple[str, str]] = []

    def confirmation(self, call: ToolCall) -> None:
        return None

    def status(self, call: ToolCall) -> str:
        return "正在记提醒……"

    async def run(self, call: ToolCall, approved: bool = False) -> str:
        self.ran.append((call.name, call.arguments))
        return "记下了：10 分钟后喝水"


class FakeConn:
    def __init__(self, factory: Any, brain: AgentBrain, script: list) -> None:
        self.bridge = factory(None)
        self.brain = brain
        self.script = script
        self.sessions: list[dict] = []
        self.prompts: list[str] = []
        self.tool_results: list[str] = []
        self.fail: Exception | None = None

    async def initialize(self, **kw: Any) -> Any:
        return schema.InitializeResponse(protocol_version=1)

    async def new_session(self, cwd: str, mcp_servers: list, system_prompt: Any = None, **kw: Any) -> Any:
        if self.fail:
            raise self.fail
        self.sessions.append({"servers": mcp_servers, "system": system_prompt})
        return schema.NewSessionResponse(session_id=f"s{len(self.sessions)}")

    async def set_config_option(self, **kw: Any) -> Any:
        return schema.SetSessionConfigOptionResponse(config_options=[])

    async def cancel(self, session_id: str, **kw: Any) -> None:
        pass

    async def prompt(self, session_id: str, prompt: list, **kw: Any) -> Any:
        self.prompts.append(prompt[0].text)
        for step, arg in self.script:
            if step == "say":
                await self.bridge.session_update(session_id, schema.AgentMessageChunk(content=schema.TextContentBlock(type="text", text=arg)))
            elif step == "tool":  # 它通过 MCP 调艾拉的工具
                self.tool_results.append(await self.brain.tool_call(*arg))
        return schema.PromptResponse(stop_reason="end_turn")


def make(tmp_path: Path, script: list, engine: str = "codex") -> tuple[AgentBrain, list[FakeConn]]:
    conns: list[FakeConn] = []
    holder: dict[str, AgentBrain] = {}

    @asynccontextmanager
    async def spawn(factory: Any, *a: Any, **kw: Any):
        conn = FakeConn(factory, holder["b"], script)
        conns.append(conn)
        yield conn, None

    brain = AgentBrain(engine, tmp_path, "http://127.0.0.1:9", spawn=spawn, launcher=lambda e, d: ("node", ["x.js"], {}))
    holder["b"] = brain
    return brain, conns


def convo(*turns: str) -> list[dict]:
    msgs = [{"role": "system", "content": "你是艾拉。"}]
    for i, text in enumerate(turns):
        msgs.append({"role": "user" if i % 2 == 0 else "assistant", "content": text})
    return msgs


async def collect(brain: AgentBrain, messages: list[dict], toolbox: Any = None) -> list[dict]:
    return [e async for e in run_agent(brain, messages, toolbox or FakeToolbox(), Confirmations(timeout=5))]


def test_reply_goes_through_the_normal_chat_flow_with_persona(tmp_path: Path) -> None:
    brain, conns = make(tmp_path, [("say", "[happy]你好呀。"), ("say", "[nod]今天也加油。")])
    events = asyncio.run(collect(brain, convo("<背景>\n今天是…\n</背景>\n\n你好")))
    texts = "".join(e.get("text", "") for e in events if e["type"] == "text")
    assert "你好呀" in texts and "[happy]" not in texts  # 标签照常解析成表情
    assert any(e["type"] == "emotion" for e in events)
    session = conns[0].sessions[0]
    assert session["system"].mode == "append" and session["system"].content[0].text == "你是艾拉。"  # Codex 接在后面
    assert session["servers"][0].url == "http://127.0.0.1:9/chat/bridge"
    assert conns[0].prompts[0].endswith("你好") and "<背景>" in conns[0].prompts[0]  # 记忆原样交过去


def test_claude_gets_the_persona_as_its_whole_system_prompt(tmp_path: Path) -> None:
    brain, conns = make(tmp_path, [("say", "好")], engine="claude")
    asyncio.run(collect(brain, convo("你好")))
    assert conns[0].sessions[0]["system"].mode == "override"


def test_tools_are_run_by_aila_and_the_result_goes_back(tmp_path: Path) -> None:
    brain, conns = make(tmp_path, [("say", "[think]我记一下。"), ("tool", ("set_reminder", {"what": "喝水"})), ("say", "记好了。")])
    toolbox = FakeToolbox()
    events = asyncio.run(collect(brain, convo("10 分钟后提醒我喝水"), toolbox))
    assert toolbox.ran == [("set_reminder", '{"what": "喝水"}')]  # 艾拉的对话流程执行的
    assert conns[0].tool_results == ["记下了：10 分钟后喝水"]  # 结果交回给它
    assert any(e["type"] == "status" for e in events)
    assert "记好了" in "".join(e.get("text", "") for e in events if e["type"] == "text")


def test_same_conversation_keeps_the_session_and_sends_only_the_new_line(tmp_path: Path) -> None:
    brain, conns = make(tmp_path, [("say", "好")])

    async def go() -> None:
        await collect(brain, convo("第一句"))
        await collect(brain, convo("第一句", "好", "第二句"))
        await collect(brain, convo("换了个话题"))  # 对不上：重新开，带上前情

    asyncio.run(go())
    conn = conns[0]
    assert len(conn.sessions) == 2
    assert conn.prompts[1] == "第二句"
    assert conn.prompts[2] == "换了个话题"


def test_recap_when_reopening_mid_conversation(tmp_path: Path) -> None:
    brain, conns = make(tmp_path, [("say", "好")])
    asyncio.run(collect(brain, convo("我叫小林", "你好小林", "我刚说我叫什么")))
    assert "对方：我叫小林" in conns[0].prompts[0] and conns[0].prompts[0].endswith("我刚说我叫什么")


def test_only_its_own_tools_are_allowed(tmp_path: Path) -> None:
    brain, _ = make(tmp_path, [])
    brain._specs = [{"name": "set_reminder"}]
    opts = [schema.PermissionOption(option_id="y", name="y", kind="allow_once"),
            schema.PermissionOption(option_id="n", name="n", kind="reject_once")]
    ok = brain.on_permission(schema.ToolCallUpdate(tool_call_id="1", title="mcp.aila_chat.set_reminder"), opts)
    assert ok.outcome.option_id == "y"
    for title, raw in [("rm -rf x", None), ("mcp.aila_chat.other", None), ("mcp.aila_chat.set_reminder", {"command": "del x"})]:
        res = brain.on_permission(schema.ToolCallUpdate(tool_call_id="2", title=title, raw_input=raw), opts)
        assert res.outcome.option_id == "n"


def test_not_logged_in_is_said_plainly(tmp_path: Path) -> None:
    brain, conns = make(tmp_path, [])

    async def go() -> None:
        with pytest.raises(BrainError, match="还没登录"):
            async def failing_new_session(*a: Any, **k: Any) -> Any:
                raise acp.RequestError(-32000, "Authentication required")
            await brain._open("你是艾拉", "sig")  # 先连上
            conns[0].new_session = failing_new_session  # type: ignore[method-assign]
            await brain._open("你是艾拉", "sig2")

    asyncio.run(go())


def test_chat_bridge_needs_the_brain_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    brain, _ = make(tmp_path, [])
    brain._specs = [{"name": "set_reminder", "description": "", "inputSchema": {"type": "object"}}]
    monkeypatch.setattr(deps, "get_chat_brain", lambda: brain)
    client = TestClient(app)
    rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    assert client.post("/chat/bridge", json=rpc).status_code == 401
    assert client.post("/chat/bridge", json=rpc, headers={"Authorization": "Bearer nope"}).status_code == 401
    got = client.post("/chat/bridge", json=rpc, headers={"Authorization": f"Bearer {brain.token}"}).json()
    assert [t["name"] for t in got["result"]["tools"]] == ["set_reminder"]
    # 不在对话里时调工具：不执行
    call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "set_reminder", "arguments": {}}}
    out = client.post("/chat/bridge", json=call, headers={"Authorization": f"Bearer {brain.token}"}).json()
    assert "没有执行" in out["result"]["content"][0]["text"]


def test_settings_choose_the_brain(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from dataclasses import replace

    monkeypatch.setattr(deps, "settings", replace(deps.settings, chat_brain="", data_dir=tmp_path))
    assert deps.get_chat_brain() is None
    monkeypatch.setattr(deps, "settings", replace(deps.settings, chat_brain="claude", data_dir=tmp_path))
    monkeypatch.setattr(deps, "_brains", {})
    brain = deps.get_chat_brain()
    assert isinstance(brain, AgentBrain) and brain.name == "Claude Code" and deps.get_chat_brain() is brain
