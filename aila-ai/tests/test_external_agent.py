"""本机 Agent（Codex、Claude Code）通过 ACP 当会话的大脑：用假的 ACP 连接测，不起真的程序、不联网。"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from acp import schema

from app.confirm import Confirmations
from app.runtime import localagents
from app.runtime.external import ASK_NOTE, ExternalAgent, codex_model
from app.runtime.localagents import LocalAgentError, detect, find_program
from app.runtime.permissions import Policy, Rules
from app.runtime.runner import Runtime, RuntimeError_
from app.runtime.session import SessionStore

from .test_runtime import SHELL, Scripted

ALLOW = schema.PermissionOption(option_id="yes", name="Yes", kind="allow_once")
ALWAYS = schema.PermissionOption(option_id="always", name="Always", kind="allow_always")
NO = schema.PermissionOption(option_id="no", name="No", kind="reject_once")


class FakeConn:
    """按剧本演一轮：剧本里的每一步是 (动作, 参数)"""

    def __init__(self, bridge_factory, script: list[tuple[str, Any]], load: bool = True) -> None:
        self.bridge = bridge_factory(None)
        self.script = script
        self.load = load
        self.calls: list[tuple[str, Any]] = []
        self.answers: list[str] = []
        self.forever = asyncio.Event()
        self.state = {"model": "m-default", "effort": "high", "mode": "agent"}

    def options(self) -> list[Any]:
        def select(oid: str, category: str, values: list[str]) -> Any:
            return schema.SessionConfigOptionSelect(id=oid, name=oid.title(), category=category, type="select",
                                                    current_value=self.state[oid],
                                                    options=[schema.SessionConfigSelectOption(value=v, name=v.upper()) for v in values])
        return [select("mode", "mode", ["read-only", "agent", "default"]), select("model", "model", ["m-default", "m-fast", "m-big"]),
                select("effort", "thought_level", ["low", "high"])]

    async def initialize(self, **kw: Any) -> Any:
        return schema.InitializeResponse(protocol_version=1, agent_capabilities=schema.AgentCapabilities(load_session=self.load))

    async def new_session(self, cwd: str, **kw: Any) -> Any:
        self.calls.append(("new", cwd))
        return schema.NewSessionResponse(session_id="acp-1", config_options=self.options())

    async def load_session(self, cwd: str, session_id: str, **kw: Any) -> Any:
        self.calls.append(("load", session_id))
        # 加载时重放以前的对话：不该记进日志
        await self.bridge.session_update(session_id, schema.AgentMessageChunk(content=schema.TextContentBlock(type="text", text="以前说过的")))
        return schema.LoadSessionResponse(config_options=self.options())

    async def set_config_option(self, config_id: str, session_id: str, value: Any, **kw: Any) -> Any:
        self.calls.append(("config", (config_id, value)))
        self.state[config_id] = value
        return schema.SetSessionConfigOptionResponse(config_options=self.options())

    async def cancel(self, session_id: str, **kw: Any) -> None:
        self.calls.append(("cancel", session_id))
        self.forever.set()

    async def prompt(self, session_id: str, prompt: list[Any], **kw: Any) -> Any:
        self.calls.append(("prompt", prompt[0].text))
        b = self.bridge
        for step, arg in self.script:
            if step == "say":
                await b.session_update(session_id, schema.AgentMessageChunk(content=schema.TextContentBlock(type="text", text=arg)))
            elif step == "tool":
                await b.session_update(session_id, arg)
            elif step == "ask":
                res = await b.request_permission(session_id, arg, [ALLOW, ALWAYS, NO])
                self.answers.append(res.outcome.option_id)
                if res.outcome.option_id == "no":
                    return schema.PromptResponse(stop_reason="cancelled")
            elif step == "wait":
                await self.forever.wait()
                return schema.PromptResponse(stop_reason="cancelled")
            elif step == "update":
                await b.session_update(session_id, arg)
        return schema.PromptResponse(stop_reason="end_turn")


def edit_call(root: Path, cid: str = "e1") -> Any:
    return schema.ToolCallStart(tool_call_id=cid, title="Edit calc.py", kind="edit", status="pending",
                                locations=[schema.ToolCallLocation(path=str(root / "calc.py"))],
                                content=[schema.FileEditToolCallContent(type="diff", path=str(root / "calc.py"),
                                                                        old_text="a = 1\n", new_text="a = 1\n# 改了\n")])


def run_call(cid: str, command: str) -> Any:
    return schema.ToolCallStart(tool_call_id=cid, title=command, kind="execute", status="pending", raw_input={"command": command})


def done(cid: str) -> Any:
    return schema.ToolCallProgress(tool_call_id=cid, status="completed")


class Harness:
    def __init__(self, tmp_path: Path, mode: str, script: list, engine: str = "codex", load: bool = True) -> None:
        (tmp_path / "proj").mkdir(exist_ok=True)
        self.root = tmp_path / "proj"
        self.store = SessionStore(tmp_path / "sessions")
        self.session = self.store.create(self.root, mode, "Codex", "", {"engine": engine})
        self.confirms = Confirmations(timeout=5)
        self.conns: list[FakeConn] = []
        self.script, self.load = script, load
        self.agent = self.make()

    def make(self) -> ExternalAgent:
        @asynccontextmanager
        async def spawn(factory, *a: Any, **kw: Any):
            conn = FakeConn(factory, self.script, self.load)
            self.conns.append(conn)
            yield conn, None

        policy = Policy(self.root, Rules(self.root.parent / "rules.json"), self.session.mode)
        agent = ExternalAgent(self.session, self.session.events[0]["engine"], policy, self.confirms, self.root.parent,
                              spawn=spawn, launcher=lambda e, d: ("node", ["adapter.js"], {}))
        return agent

    async def turn(self, text: str, answer: bool | None = None) -> list[dict]:
        events = []
        async for e in self.agent.turn(text):
            events.append(e)
            if e.get("type") == "confirm" and answer is not None:
                self.confirms.answer(e["id"], answer)
        return events


def types(events: list[dict]) -> list[str]:
    return [e["type"] for e in events]


def test_ask_mode_shows_code_made_diff_and_edits_after_yes(tmp_path: Path) -> None:
    h = Harness(tmp_path, "ask", [("say", "我来改。"), ("tool", None), ("ask", None), ("update", done("e1")), ("say", "改好了。")])
    h.script[1] = ("tool", edit_call(h.root))
    h.script[2] = ("ask", schema.ToolCallUpdate(tool_call_id="e1", kind="edit", title="Edit files"))
    events = asyncio.run(h.turn("加一行注释", answer=True))
    confirm = next(e for e in events if e["type"] == "confirm")
    assert confirm["kind"] == "edit" and confirm["call_id"] == "e1"
    assert "+# 改了" in confirm["detail"] and "a/calc.py" in confirm["detail"]  # diff 是这边算的
    tool = next(e for e in events if e["type"] == "tool")
    assert tool["status"] == "ok" and tool["file"] == "calc.py" and "（+1 −0）" in tool["content"]
    assert events[-1]["type"] == "turn_end" and events[-1]["reason"] == "done"
    conn = h.conns[0]
    assert ("config", ("mode", "read-only")) in conn.calls  # Codex 开在只读沙箱
    assert conn.answers == ["yes"]
    assert conn.calls[[c[0] for c in conn.calls].index("prompt")][1].startswith(ASK_NOTE)  # 第一句前面有说明
    assert any(e["type"] == "engine_session" and e["id"] == "acp-1" for e in h.session.events)
    # 话和工具都按艾拉的日志记：assistant 带着工具调用
    said = [e for e in h.session.events if e["type"] == "assistant"]
    assert said[0]["text"] == "我来改。" and said[0]["tool_calls"][0]["name"] == "edit_file"


def test_rejected_stops_the_turn(tmp_path: Path) -> None:
    h = Harness(tmp_path, "ask", [])
    h.script.extend([("tool", edit_call(h.root)), ("ask", schema.ToolCallUpdate(tool_call_id="e1", kind="edit"))])
    events = asyncio.run(h.turn("改", answer=False))
    assert h.conns[0].answers == ["no"]
    assert events[-1]["reason"] == "rejected"
    assert next(e for e in events if e["type"] == "tool")["status"] == "rejected"


def test_auto_mode_allows_edits_but_dangerous_commands_still_ask(tmp_path: Path) -> None:
    h = Harness(tmp_path, "auto", [])
    h.script.extend([
        ("tool", edit_call(h.root)), ("ask", schema.ToolCallUpdate(tool_call_id="e1", kind="edit")), ("update", done("e1")),
        ("tool", run_call("c1", "npm test")), ("ask", schema.ToolCallUpdate(tool_call_id="c1", kind="execute", raw_input={"command": "npm test"})),
        ("tool", run_call("c2", "git push --force")),
        ("ask", schema.ToolCallUpdate(tool_call_id="c2", kind="execute", raw_input={"command": "git push --force"})),
    ])
    events = asyncio.run(h.turn("改完推上去", answer=False))
    confirms = [e for e in events if e["type"] == "confirm"]
    assert [c["detail"] for c in confirms] == ["git push --force"]  # 只有危险命令问了，给你看的是命令原文
    assert h.conns[0].answers == ["yes", "yes", "no"]


def test_dangerous_second_line_still_asks_and_titles_are_short(tmp_path: Path) -> None:
    h = Harness(tmp_path, "auto", [])
    title = "echo hi\nrm -rf build"
    start = schema.ToolCallStart(tool_call_id="c1", title=title, kind="execute", status="pending")
    h.script.extend([("tool", start), ("ask", schema.ToolCallUpdate(tool_call_id="c1", kind="execute", title=title))])
    events = asyncio.run(h.turn("清理", answer=False))
    confirm = next(e for e in events if e["type"] == "confirm")
    assert "rm -rf build" in confirm["detail"]  # 没有 raw_input 时按完整的标题判断，第二行的删除也要问
    starts = [e for e in events if e["type"] == "tool_start"]
    assert starts[0]["status"] == "echo hi"
    read = schema.ToolCallStart(tool_call_id="r1", title=f"Read file '{h.root}\\calc.py'", kind="read", status="pending")
    h.script[:] = [("tool", read)]
    events = asyncio.run(h.turn("读"))
    assert next(e for e in events if e["type"] == "tool_start")["status"] == "Read file 'calc.py'"


def test_read_only_denies_edits_without_asking(tmp_path: Path) -> None:
    h = Harness(tmp_path, "read_only", [])
    h.script.extend([("tool", edit_call(h.root)), ("ask", schema.ToolCallUpdate(tool_call_id="e1", kind="edit"))])
    events = asyncio.run(h.turn("改"))
    assert not [e for e in events if e["type"] == "confirm"]
    assert any(e["type"] == "note" and "只读" in e["text"] for e in events)
    assert events[-1]["reason"] == "denied" and h.conns[0].answers == ["no"]


def test_resume_loads_the_old_session_without_recording_replay(tmp_path: Path) -> None:
    h = Harness(tmp_path, "ask", [("say", "第一轮")])
    asyncio.run(h.turn("你好"))
    before = len(h.session.events)
    h.script[:] = [("say", "第二轮")]
    h.agent = h.make()  # 艾拉重启了：新的进程
    events = asyncio.run(h.turn("接着说"))
    conn = h.conns[-1]
    assert ("load", "acp-1") in conn.calls and not any(c[0] == "new" for c in conn.calls)
    assert not conn.calls[[c[0] for c in conn.calls].index("prompt")][1].startswith(ASK_NOTE)
    texts = [e.get("text") for e in h.session.events[before:] if e["type"] == "assistant"]
    assert texts == ["第二轮"]  # 重放的「以前说过的」没记
    assert events[-1]["reason"] == "done"


def test_no_load_support_starts_fresh(tmp_path: Path) -> None:
    h = Harness(tmp_path, "ask", [("say", "好")], load=False)
    asyncio.run(h.turn("一"))
    h.agent = h.make()
    asyncio.run(h.turn("二"))
    assert [c[0] for c in h.conns[-1].calls if c[0] in ("new", "load")] == ["new"]


def test_interrupt_cancels_and_records(tmp_path: Path) -> None:
    h = Harness(tmp_path, "ask", [("say", "做着"), ("tool", run_call("c1", "sleep 100")), ("wait", None)])

    async def go() -> None:
        async def consume() -> None:
            async for _ in h.agent.turn("慢慢做"):
                pass

        task = asyncio.ensure_future(consume())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    assert ("cancel", "acp-1") in h.conns[0].calls
    tail = [e for e in h.session.events if e["type"] in ("tool", "turn_end")]
    assert tail[-2]["status"] == "interrupted" and tail[-1]["reason"] == "interrupted"


def test_system_error_is_an_error(tmp_path: Path) -> None:
    said = '{"type":"error","error":{"message":"The \'gpt-x\' model is not supported when using Codex with a ChatGPT account."}}'
    info = schema.SessionInfoUpdate(field_meta={"codex": {"threadStatus": {"type": "systemError"}}})
    h = Harness(tmp_path, "ask", [("update", info), ("say", said)])
    events = asyncio.run(h.turn("hi"))
    assert events[-1]["reason"] == "error" and "模型" in events[-1]["error"]


def test_claude_runs_in_manual_mode(tmp_path: Path) -> None:
    h = Harness(tmp_path, "ask", [("say", "好")], engine="claude")
    asyncio.run(h.turn("hi"))
    conn = h.conns[0]
    assert ("config", ("mode", "default")) in conn.calls
    assert not conn.calls[[c[0] for c in conn.calls].index("prompt")][1].startswith(ASK_NOTE)


def test_plan_becomes_todo_card(tmp_path: Path) -> None:
    plan = schema.AgentPlanUpdate(entries=[schema.PlanEntry(content="读代码", priority="high", status="completed"),
                                           schema.PlanEntry(content="改代码", priority="high", status="in_progress")])
    h = Harness(tmp_path, "ask", [("update", plan)])
    events = asyncio.run(h.turn("做"))
    todo = next(e for e in events if e["type"] == "tool")
    assert todo["name"] == "todo_write" and [t["status"] for t in todo["todos"]] == ["completed", "in_progress"]


# ---------- 找程序、登录 ----------


def test_detect_finds_programs_and_login(tmp_path: Path) -> None:
    npm = tmp_path / "npm"
    exe = npm / "node_modules" / "@openai" / "codex" / "node_modules" / "@openai" / "codex-win32-x64" / "vendor" / "x86_64" / "bin" / "codex.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    acp_dir = tmp_path / "acp" / "node_modules" / "@agentclientprotocol"
    for e in localagents.ENGINES.values():
        if e.native:
            continue
        script = tmp_path / "acp" / "node_modules" / e.script
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("")
    paths = {"codex": str(npm / "codex.cmd"), "claude": "C:/x/claude.exe", "node": "C:/node.exe"}

    def run(args: list[str], timeout: float = 0) -> tuple[int, str]:
        if args[1:] == ["--version"]:
            return 0, "1.0\n"
        if args[1:] == ["login", "status"]:
            return 0, "Logged in using ChatGPT"
        return 0, '{"loggedIn": false, "authMethod": "none"}'

    import os
    os.environ["AILA_ACP_DIR"] = str(tmp_path / "acp")
    try:
        found = {a.id: a for a in detect(tmp_path, which=paths.get, run=run, local_app_data="")}
    finally:
        os.environ.pop("AILA_ACP_DIR")
    assert found["codex"].path == str(exe) and found["codex"].ready and found["codex"].logged_in
    assert not found["claude"].ready and "claude auth login" in found["claude"].hints[0]
    assert acp_dir.is_dir()


def test_detect_without_programs_or_node(tmp_path: Path) -> None:
    found = {a.id: a for a in detect(tmp_path, which=lambda name: None, run=lambda *a, **k: (-1, ""), local_app_data="")}
    assert not found["codex"].ready and "没找到" in found["codex"].problem
    assert find_program(localagents.ENGINES["claude"], which=lambda n: None, local_app_data="") is None
    with pytest.raises(LocalAgentError):
        localagents.launch("codex", tmp_path, which=lambda n: None, local_app_data="")


def test_codex_model_reads_only_the_global_setting(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text('model = "gpt-6-astra"\n[profiles.x]\nmodel = "other"\n', encoding="utf-8")
    assert codex_model(tmp_path) == "gpt-6-astra"
    assert codex_model(tmp_path / "nope") == ""


# ---------- 接到 Runtime ----------


def test_runtime_creates_external_sessions(tmp_path: Path) -> None:
    rt = Runtime(tmp_path / "data", lambda: Scripted([["好"]]), shell=SHELL)
    proj = tmp_path / "p"
    proj.mkdir()
    s = rt.create(str(proj), "ask", engine="claude")
    assert s.summary()["engine"] == "claude" and s.summary()["model"] == "Claude Code"
    assert isinstance(rt.agent(s.id), ExternalAgent)
    with pytest.raises(RuntimeError_):
        rt.create(None, "ask", kind="computer", engine="codex")
    with pytest.raises(RuntimeError_):
        rt.create(str(proj), "ask", engine="gemini")
    with pytest.raises(RuntimeError_, match="直接用话跟它说"):
        rt.run(s.id, "", review=True)
    with pytest.raises(RuntimeError_):
        rt.undo(s.id)


# ---------- 换模型 ----------


def test_options_offer_model_and_effort_but_not_mode(tmp_path: Path) -> None:
    h = Harness(tmp_path, "ask", [])
    options = asyncio.run(h.agent.prepare())
    assert [o["id"] for o in options] == ["model", "effort"]  # 权限模式不给换
    assert options[0]["current"] == "m-default" and [c["value"] for c in options[0]["choices"]] == ["m-default", "m-fast", "m-big"]


def test_switching_model_now_and_after_restart(tmp_path: Path) -> None:
    h = Harness(tmp_path, "ask", [("say", "好")])

    async def go() -> None:
        await h.agent.prepare()
        options = await h.agent.set_option("model", "m-big")
        assert next(o for o in options if o["id"] == "model")["current"] == "m-big"
        assert ("config", ("model", "m-big")) in h.conns[0].calls
        with pytest.raises(LocalAgentError):
            await h.agent.set_option("model", "not-a-model")
        with pytest.raises(LocalAgentError):
            await h.agent.set_option("mode", "agent")  # 权限模式不给换

    asyncio.run(go())
    h.agent = h.make()  # 重启：新进程默认又是 m-default，加载会话后换回你选的
    asyncio.run(h.turn("接着"))
    assert ("config", ("model", "m-big")) in h.conns[-1].calls
    assert next(o for o in h.agent.options if o["id"] == "model")["current"] == "m-big"


def test_pick_while_running_applies_next_turn(tmp_path: Path) -> None:
    h = Harness(tmp_path, "ask", [("say", "好")])
    asyncio.run(h.turn("一"))
    h.agent._busy = True  # 正在做：先记下
    asyncio.run(h.agent.set_option("effort", "low"))
    assert ("config", ("effort", "low")) not in h.conns[0].calls
    h.agent._busy = False
    asyncio.run(h.turn("二"))
    assert ("config", ("effort", "low")) in h.conns[0].calls


def test_runtime_options_routes_reject_aila_sessions(tmp_path: Path) -> None:
    rt = Runtime(tmp_path / "data", lambda: Scripted([["好"]]), shell=SHELL)
    proj = tmp_path / "p"
    proj.mkdir()
    s = rt.create(str(proj), "ask")
    with pytest.raises(RuntimeError_, match="设置"):
        asyncio.run(rt.engine_options(s.id))


def test_newest_codex_wins_including_the_desktop_app_copy(tmp_path: Path) -> None:
    app = tmp_path / "OpenAI" / "Codex" / "bin" / "abc123" / "codex.exe"
    app.parent.mkdir(parents=True)
    app.write_text("")
    npm = str(Path("C:/npm/codex.exe"))
    versions = {npm: "codex-cli 0.155.1", str(app): "codex-cli 0.167.0-alpha.3.2"}
    run = lambda args, timeout=0: (0, versions.get(args[0], ""))  # noqa: E731
    engine = localagents.ENGINES["codex"]
    assert find_program(engine, which=lambda n: "C:/npm/codex.exe", run=run, local_app_data=str(tmp_path)) == str(app)
    versions[npm] = "codex-cli 0.172.0"
    assert find_program(engine, which=lambda n: "C:/npm/codex.exe", run=run, local_app_data=str(tmp_path)) == npm
    # 只有桌面版带的那份也能用
    assert find_program(engine, which=lambda n: None, run=run, local_app_data=str(tmp_path)) == str(app)


def test_own_dispatch_tools_pass_without_asking_but_commands_still_ask(tmp_path: Path) -> None:
    h = Harness(tmp_path, "ask", [])
    own = schema.ToolCallStart(tool_call_id="m1", title="mcp.aila.wait_work", kind="other", status="pending")
    sneaky = schema.ToolCallStart(tool_call_id="m2", title="mcp.aila.delegate", kind="execute", status="pending", raw_input={"command": "rm -rf /"})
    h.script.extend([("tool", own), ("ask", schema.ToolCallUpdate(tool_call_id="m1", title="mcp.aila.wait_work", kind="other")),
                     ("tool", sneaky), ("ask", schema.ToolCallUpdate(tool_call_id="m2", title="mcp.aila.delegate", kind="execute", raw_input={"command": "rm -rf /"}))])
    events = asyncio.run(h.turn("派活", answer=False))
    confirms = [e for e in events if e["type"] == "confirm"]
    assert [c["detail"] for c in confirms] == ["rm -rf /"]  # 艾拉自己的工具直接放行，带命令的照样问
    assert h.conns[0].answers == ["yes", "no"]


def test_deferred_tools_are_asked_as_the_real_tool(tmp_path: Path) -> None:
    """CodeBuddy 的外部工具、不常用的工具包在 DeferExecuteTool 里：按里面的真工具问，不能都当成一条叫 DeferExecuteTool 的命令
    （那样「以后都不用问」会放过以后所有包着的工具）"""
    h = Harness(tmp_path, "ask", [], engine="codebuddy")
    def wrapped(cid: str, name: str, params: dict) -> list:
        start = schema.ToolCallStart(tool_call_id=cid, title="DeferExecuteTool", status="pending", raw_input={})
        ask = schema.ToolCallUpdate(tool_call_id=cid, raw_input={"toolName": name, "params": params})  # 来问时不带标题
        return [("tool", start), ("ask", ask)]
    h.script.extend(wrapped("d1", "mcp__aila__work_status", {}) + wrapped("d2", "mcp__github__create_issue", {"title": "x"})
                    + wrapped("d3", "CronCreate", {"cron": "1 * * * *", "prompt": "喝水"}))
    events = asyncio.run(h.turn("做", answer=True))
    confirms = [e for e in events if e["type"] == "confirm"]
    assert [(c["kind"], c["action"]) for c in confirms] == [("mcp", "mcp__github__create_issue"), ("command", "运行命令 CronCreate")]
    assert '"title": "x"' in confirms[0]["detail"] and "CronCreate" in confirms[1]["detail"] and "喝水" in confirms[1]["detail"]
    assert len(h.conns[0].answers) == 3  # 艾拉自己的派工工具没问就放行（只问了两次）


def test_usage_limit_is_said_plainly(tmp_path: Path) -> None:
    class Boom(Exception):
        pass
    h = Harness(tmp_path, "ask", [("say", "You've hit your usage limit. Upgrade to Pro or try again at Nov 27th.")])
    import acp
    async def broken_prompt(*a, **k):
        await h.conns[-1].bridge.session_update("acp-1", schema.AgentMessageChunk(content=schema.TextContentBlock(type="text", text="You've hit your usage limit. Try again at Nov 27th.")))
        raise acp.RequestError(-32603, "Internal error")
    async def go() -> list:
        await h.agent.prepare()
        h.conns[-1].prompt = broken_prompt
        return [e async for e in h.agent.turn("hi")]
    events = asyncio.run(go())
    assert events[-1]["reason"] == "error" and "额度用完了" in events[-1]["error"]


def test_antigravity_is_native_acp(tmp_path: Path) -> None:
    """Antigravity 自己就会说 ACP：不用 Node、不用适配器，起 agy acp --process-per-session（这样才收 HTTP 的 MCP）"""
    agy = tmp_path / "agy" / "bin" / "agy.exe"
    agy.parent.mkdir(parents=True)
    agy.write_text("")
    run = lambda args, timeout=0: (0, "1.5.18") if args[1:] == ["--version"] else (0, "")  # noqa: E731
    found = {a.id: a for a in detect(tmp_path, which=lambda n: None, run=run, local_app_data=str(tmp_path))}
    a = found["antigravity"]
    assert a.ready and a.native and a.path == str(agy) and a.logged_in is None and a.version == "1.5.18"
    program, args, env = localagents.launch("antigravity", tmp_path, which=lambda n: None, local_app_data=str(tmp_path))
    assert program == str(agy) and args == ["acp", "--process-per-session"]


def test_antigravity_tasks_ask_before_changes(tmp_path: Path) -> None:
    h = Harness(tmp_path, "ask", [("say", "好")], engine="antigravity")
    asyncio.run(h.turn("hi"))
    conn = h.conns[0]
    assert ("config", ("mode", "default")) in conn.calls
    assert not conn.calls[[c[0] for c in conn.calls].index("prompt")][1].startswith(ASK_NOTE)


def test_account_verification_is_an_error(tmp_path: Path) -> None:
    h = Harness(tmp_path, "ask", [("say", "\n⚠ Verification Required\n")], engine="antigravity")
    events = asyncio.run(h.turn("改一行"))
    assert events[-1]["reason"] == "error" and "验证你的账号" in events[-1]["error"]


def test_tools_without_kind_are_recognised_by_name(tmp_path: Path) -> None:
    """CodeBuddy 不报工具种类，只报 Edit、Bash 这些名字：改文件要按改文件问（diff 由这边算），不能当成叫 Edit 的命令"""
    h = Harness(tmp_path, "ask", [], engine="codebuddy")
    (h.root / "calc.py").write_text("a = 1\n", encoding="utf-8")
    raw = {"file_path": str(h.root / "calc.py"), "old_string": "a = 1", "new_string": "a = 2"}
    edit = schema.ToolCallStart(tool_call_id="e1", title="Edit", status="pending")  # 开始时不带参数，只在来问时带
    bash = schema.ToolCallStart(tool_call_id="b1", title="Bash", status="pending", raw_input={"command": "git status"})
    h.script.extend([("tool", edit), ("ask", schema.ToolCallUpdate(tool_call_id="e1", title="Edit", raw_input=raw)), ("update", done("e1")),
                     ("tool", bash), ("ask", schema.ToolCallUpdate(tool_call_id="b1", title="Bash", raw_input={"command": "git status"}))])
    events = asyncio.run(h.turn("改", answer=True))
    confirms = [e for e in events if e["type"] == "confirm"]
    assert confirms[0]["kind"] == "edit" and "-a = 1" in confirms[0]["detail"] and "+a = 2" in confirms[0]["detail"]
    assert confirms[0]["action"] == "改 calc.py" and confirms[0]["tool"] == "edit_file"
    starts = [e for e in events if e["type"] == "tool_start"]
    assert [s["name"] for s in starts] == ["edit_file", "run_command"]
    assert ("config", ("mode", "default")) in h.conns[0].calls  # CodeBuddy 的「Always Ask」
