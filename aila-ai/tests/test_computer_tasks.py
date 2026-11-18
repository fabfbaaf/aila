"""电脑任务的真实运行链路：会话类型、权限确认、长任务续接以及接口和终端。"""

import io
import tempfile
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from app.cli.app import Terminal, headless, open_session, parse_args
from app.cli.backend import EmbeddedBackend, HttpBackend
from app.cli.commands import next_mode
from app.deps import get_runtime
from app.main import app
from app.runtime.loop import AgentConfig
from app.runtime.permissions import ComputerPolicy
from app.runtime.planning import plan_state
from app.runtime.runner import Runtime, RuntimeError_
from app.runtime.session import SessionStore
from app.runtime.workspace import ComputerWorkspace

from .test_runtime import SHELL, Scripted, call

PLAN = {"goal": "整理测试笔记", "stages": [
    {"title": "列出方案", "goal": "查看笔记", "verify": "笔记内容可读"},
    {"title": "写入结果", "goal": "输出说明", "verify": "结果文件存在"},
]}


@pytest.fixture
def tmp_path() -> Iterator[Path]:
    # Windows 默认临时目录在 AppData，电脑任务必须把它当受保护目录。
    with tempfile.TemporaryDirectory(prefix=".computer-task-", dir=Path.cwd()) as folder:
        yield Path(folder)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    (home / "note.txt").write_text("原始笔记", encoding="utf8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr("app.runtime.runner.computer_shell", lambda: SHELL)
    monkeypatch.setattr("app.runtime.runner.build_computer_context",
                        lambda root, shell, inventory: "<电脑环境>测试环境</电脑环境>")
    return home


def runtime(tmp_path: Path, model: Scripted, **kwargs) -> Runtime:  # noqa: ANN003
    return Runtime(tmp_path / "data", lambda: model, shell=SHELL, **kwargs)


async def collect(rt: Runtime, sid: str, text: str = "接着做", *, answers: list[tuple[bool, bool]] | None = None,
                  plan: bool = False) -> list[dict]:
    pending = iter(answers or [])
    events = []
    async for event in rt.run(sid, text, plan=plan):
        events.append(event)
        if event["type"] == "confirm":
            approved, remember = next(pending)
            rt.confirms.answer(event["id"], approved, remember)
    return events


def test_computer_creation_guards_and_legacy_project(home: Path, tmp_path: Path) -> None:
    rt = runtime(tmp_path, Scripted([["好"]]))
    session = rt.create("Z:/ignored", kind="computer", budget=0.5)
    assert session.cwd == home and session.kind == "computer"
    assert session.summary()["kind"] == "computer" and session.budget == 0.5
    assert session.events[0]["kind"] == "computer" and session.context == "<电脑环境>测试环境</电脑环境>"
    agent = rt.agent(session.id)
    assert isinstance(agent.tools.ws, ComputerWorkspace) and isinstance(agent.policy, ComputerPolicy)
    assert {"propose_plan", "stage_done", "stage_blocked"} <= agent.tools.tools.keys()
    assert not {"remember", "explore"} & agent.tools.tools.keys()
    assert not any(name.startswith(("mcp_", "browser_", "code_")) for name in agent.tools.tools)
    for kwargs in ({"mode": "auto"}, {"isolate": True}):
        with pytest.raises(RuntimeError_):
            rt.create(kind="computer", **kwargs)
    with pytest.raises(RuntimeError_):
        rt.set_mode(session.id, "auto")
    with pytest.raises(RuntimeError_):
        rt.create()
    old = SessionStore(tmp_path / "legacy").create(home, "auto", "scripted", "旧环境")
    old.events[0].pop("kind")
    assert old.kind == "project" and old.summary()["kind"] == "project" and old.mode == "auto"
    session.append({"type": "mode", "mode": "auto"})
    assert session.mode == "ask"


@pytest.mark.anyio
async def test_computer_permissions_and_separate_command_scope(home: Path, tmp_path: Path,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    from app.runtime.shell import CommandResult

    fake_run = AsyncMock(return_value=CommandResult(0, "测试输出", 0.1, False))
    monkeypatch.setattr("app.runtime.tools.run", fake_run)
    model = Scripted([
        [call("read_file", path="note.txt"), call("run_command", command="Get-Date")],
        [call("write_file", path="result.txt", content="结果"), call("run_command", command="Start-Process notepad.exe")],
        ["完成"],
        [call("run_command", command="Start-Process notepad.exe")], ["完成"],
    ])
    rt = runtime(tmp_path, model)
    sid = rt.create(kind="computer").id
    try:
        events = await collect(rt, sid, answers=[(True, True), (True, True)])
        confirms = [e for e in events if e["type"] == "confirm"]
        assert [e["kind"] for e in confirms] == ["edit", "command"]
        assert all(e["can_remember"] for e in confirms)
        assert (home / "result.txt").read_text(encoding="utf8") == "结果"
        assert rt.computer_rules.commands("computer") == ["Start-Process notepad.exe"]
        assert not rt.rules.commands(home)
        events = await collect(rt, sid)
        assert not any(e["type"] == "confirm" for e in events)
        assert fake_run.await_count == 3
        assert rt.find_project("") == []
    finally:
        await rt.shutdown()


@pytest.mark.anyio
async def test_computer_read_only_rejects_changes(home: Path, tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    fake_run = AsyncMock()
    monkeypatch.setattr("app.runtime.tools.run", fake_run)
    model = Scripted([[call("write_file", path="no.txt", content="不可写"),
                       call("run_command", command="Start-Process notepad.exe")], ["停下"]])
    rt = runtime(tmp_path, model)
    sid = rt.create(kind="computer", mode="read_only").id
    try:
        events = await collect(rt, sid)
        assert [e["status"] for e in events if e["type"] == "tool"] == ["denied", "denied"]
        assert not (home / "no.txt").exists()
        fake_run.assert_not_awaited()
    finally:
        await rt.shutdown()


@pytest.mark.anyio
async def test_permission_downgrade_during_confirmation_cannot_write(home: Path, tmp_path: Path) -> None:
    rt = runtime(tmp_path, Scripted([[call("write_file", path="denied.txt", content="不能写")], ["停下"]]))
    sid = rt.create(kind="computer").id
    try:
        events = []
        async for event in rt.run(sid, "写测试文件"):
            events.append(event)
            if event["type"] == "confirm":
                rt.set_mode(sid, "read_only")
                rt.confirms.answer(event["id"], True, True)
        assert not (home / "denied.txt").exists()
        assert next(e for e in events if e["type"] == "tool")["status"] == "denied"
        assert not rt.agent(sid).policy.edits_allowed
    finally:
        await rt.shutdown()


@pytest.mark.anyio
async def test_computer_confirmation_rejects_changed_protection(home: Path, tmp_path: Path,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    rt = runtime(tmp_path, Scripted([[call("write_file", path="changed.txt", content="不能写")], ["停下"]]))
    sid = rt.create(kind="computer").id
    try:
        events = []
        async for event in rt.run(sid, "写测试文件"):
            events.append(event)
            if event["type"] == "confirm":
                original = rt.agent(sid).tools.ws.is_secret
                monkeypatch.setattr(rt.agent(sid).tools.ws, "is_secret",
                                    lambda path: path.name == "changed.txt" or original(path))
                rt.confirms.answer(event["id"], True, True)
        assert not (home / "changed.txt").exists()
        assert next(e for e in events if e["type"] == "tool")["status"] == "denied"
        assert not rt.agent(sid).policy.edits_allowed
    finally:
        await rt.shutdown()


@pytest.mark.anyio
async def test_computer_external_file_can_be_undone(home: Path, tmp_path: Path) -> None:
    path = tmp_path / "external.txt"
    path.write_bytes(b"old\r\n")
    model = Scripted([[call("read_file", path=str(path))],
                       [call("edit_file", path=str(path), old_string="old", new_string="new")], ["完成"]])
    rt = runtime(tmp_path, model)
    sid = rt.create(kind="computer").id
    try:
        events = await collect(rt, sid, answers=[(True, False)])
        assert next(e for e in events if e["type"] == "tool" and e["name"] == "edit_file")["status"] == "ok"
        assert path.read_bytes() == b"new\r\n"
        assert [Path(value) for value in rt.undo(sid)["restored"]] == [path]
        assert path.read_bytes() == b"old\r\n"
    finally:
        await rt.shutdown()


@pytest.mark.anyio
async def test_computer_dangerous_and_protected_confirmation_always_repeats(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.runtime.shell import CommandResult

    monkeypatch.setattr("app.runtime.tools.run", AsyncMock(return_value=CommandResult(0, "测试", 0.1, False)))
    protected = home / "AppData" / "fixture.txt"
    protected.parent.mkdir()
    protected.write_text("测试数据", encoding="utf8")
    model = Scripted([
        [call("run_command", command="Restart-Computer -WhatIf"), call("read_file", path=str(protected))], ["完成"],
        [call("run_command", command="Restart-Computer -WhatIf"), call("read_file", path=str(protected))], ["完成"],
    ])
    rt = runtime(tmp_path, model)
    sid = rt.create(kind="computer").id
    try:
        for _ in range(2):
            events = await collect(rt, sid, answers=[(True, True), (True, True)])
            confirms = [e for e in events if e["type"] == "confirm"]
            assert len(confirms) == 2 and all(not e["can_remember"] for e in confirms)
        assert rt.computer_rules.all() == {}
    finally:
        await rt.shutdown()


@pytest.mark.anyio
async def test_computer_plan_pause_reload_continue_and_undo(home: Path, tmp_path: Path) -> None:
    model = Scripted([
        [call("write_file", path="forbidden.txt", content="不应写入")],
        [call("propose_plan", **PLAN)], ["等你决定"],
        [call("read_file", path="note.txt")], [call("stage_done", summary="看过笔记", verified="内容可读")], ["第一段完成"],
    ])
    rt = runtime(tmp_path, model)
    sid = rt.create(kind="computer").id
    events = await collect(rt, sid, plan=True)
    request = model.requests[0][0][-1]["content"]
    assert "相关文件和可用的命令或脚本接口" in request and "2～8" in request
    assert "具体改动清单，等用户看过并确认" in request and "无法用「撤销这一轮」恢复" in request
    assert "相关代码在哪" not in request
    assert next(e for e in events if e["type"] == "tool")["status"] == "denied"
    assert not (home / "forbidden.txt").exists() and rt.agent(sid).policy.mode == "ask"
    assert plan_state(rt.agent(sid).session.events).decision == "proposed"
    rt.decide_plan(sid, True, pause_each=True)
    events = [e async for e in rt.drive_plan(sid)]
    assert events[-1]["type"] == "plan_pause" and events[-1]["reason"] == "pause_each"
    assert events[-1]["stage"] == 1
    context = rt.agent(sid).session.context
    await rt.shutdown()

    model2 = Scripted([[call("write_file", path="result.txt", content="整理完成")],
                       [call("stage_done", summary="结果已写入", verified="文件存在")], ["全部完成"]])
    reloaded = runtime(tmp_path, model2)
    try:
        session = reloaded.agent(sid).session
        assert session.kind == "computer" and session.context == context and not reloaded.running(sid)
        events = []
        async for e in reloaded.drive_plan(sid):
            events.append(e)
            if e["type"] == "confirm":
                reloaded.confirms.answer(e["id"], True)
        assert [e["plan_stage"] for e in events if e["type"] == "user"] == [1]
        assert events[-1]["type"] == "plan_end"
        assert plan_state(session.events).finished
        result = reloaded.undo(sid)
        assert result["restored"] == ["result.txt"] and not (home / "result.txt").exists()
    finally:
        await reloaded.shutdown()


@pytest.mark.anyio
async def test_computer_plan_budget_stops_and_can_continue(home: Path, tmp_path: Path) -> None:
    model = Scripted([[call("propose_plan", **PLAN)], ["等你决定"],
                       [call("stage_done", summary="看过", verified="通过")], ["好"],
                       [call("stage_done", summary="写好", verified="通过")], ["好"]])
    rt = runtime(tmp_path, model)
    sid = rt.create(kind="computer", budget=0).id
    try:
        rt.set_budget(sid, None)
        await collect(rt, sid, plan=True)
        rt.decide_plan(sid, True)
        rt.set_budget(sid, 0)
        before = len(model.requests)
        events = [e async for e in rt.drive_plan(sid)]
        assert events[-1]["type"] == "plan_pause" and events[-1]["text"] == "花到上限了"
        assert len(model.requests) == before
        rt.set_budget(sid, None)
        assert [e async for e in rt.drive_plan(sid)][-1]["type"] == "plan_end"
    finally:
        await rt.shutdown()


@pytest.mark.anyio
async def test_computer_auto_compaction_keeps_environment(home: Path, tmp_path: Path) -> None:
    model = Scripted([["交接摘要"], ["接着完成"]])
    rt = runtime(tmp_path, model, config=AgentConfig(context_window=10))
    session = rt.create(kind="computer")
    session.append({"type": "usage", "model": model.name, "prompt": 9, "completion": 0,
                    "cache_hit": 0, "cache_miss": 9})
    try:
        events = await collect(rt, session.id)
        assert next(e for e in events if e["type"] == "compaction")["summary"] == "交接摘要"
        assert "<电脑环境>测试环境</电脑环境>" in model.requests[1][0][1]["content"]
        assert "<之前的进展>\n交接摘要" in model.requests[1][0][1]["content"]
    finally:
        await rt.shutdown()


@pytest.mark.anyio
async def test_start_computer_task_can_request_plan(home: Path, tmp_path: Path) -> None:
    rt = runtime(tmp_path, Scripted([[call("propose_plan", **PLAN)], ["等你决定"]]))
    sid = rt.create(kind="computer").id
    try:
        rt.start(sid, PLAN["goal"], plan=True)
        events = [e async for e in rt.subscribe(sid)]
        assert any(e.get("plan_request") for e in events)
        assert plan_state(rt.agent(sid).session.events).decision == "proposed"
        assert rt.agent(sid).policy.mode == "ask" and not rt.running(sid)
    finally:
        await rt.shutdown()


@pytest.mark.anyio
async def test_computer_api_and_backends(home: Path, tmp_path: Path) -> None:
    rt = runtime(tmp_path, Scripted([["已查看"]]))
    app.dependency_overrides[get_runtime] = lambda: rt
    backend = HttpBackend("http://testserver", transport=httpx.ASGITransport(app=app))
    try:
        info = await backend.create(None, "ask", kind="computer")
        assert info["kind"] == "computer" and info["cwd"] == str(home)
        assert set(info["modes"]) == {"ask", "read_only"}
        res = await backend.client.put(f"/agent/sessions/{info['id']}/mode", json={"mode": "auto"})
        assert res.status_code == 400
        for body in ({"kind": "computer", "mode": "auto"}, {"kind": "computer", "isolate": True}, {}):
            assert (await backend.client.post("/agent/sessions", json=body)).status_code == 400
        assert (await backend.client.post("/agent/sessions", json={"kind": "other"})).status_code == 422
        task = await backend.client.post("/agent/tasks", json={"kind": "computer", "text": "看看时间"})
        assert task.status_code == 201 and task.json()["kind"] == "computer"
        assert (await backend.client.get("/agent/sessions")).json()[0]["kind"] == "computer"
        embedded = EmbeddedBackend(rt)
        local = await embedded.create(None, "read_only", kind="computer")
        assert local["kind"] == "computer"
        rt.computer_rules.allow("computer", "Start-Process notepad.exe")
        assert {"project": "computer", "commands": ["Start-Process notepad.exe"]} in await backend.rules()
        await backend.forget_rule("computer", "Start-Process notepad.exe")
        assert rt.computer_rules.commands("computer") == []
    finally:
        app.dependency_overrides.pop(get_runtime, None)
        await backend.close()
        await rt.shutdown()


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["project", "computer"])
async def test_session_creation_does_not_initialize_agent(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    rt = runtime(tmp_path, Scripted([]))
    app.dependency_overrides[get_runtime] = lambda: rt
    backend = HttpBackend("http://testserver", transport=httpx.ASGITransport(app=app))

    def unexpected_agent(sid: str):  # noqa: ANN202
        raise AssertionError("创建会话不应顺带初始化 Agent")

    monkeypatch.setattr(rt, "agent", unexpected_agent)
    try:
        body = {"kind": kind, "cwd": str(home)}
        response = await backend.client.post("/agent/sessions", json=body)
        assert response.status_code == 201
        assert response.json()["kind"] == kind
        local = await EmbeddedBackend(rt).create(str(home), "ask", kind=kind)
        assert local["kind"] == kind
    finally:
        app.dependency_overrides.pop(get_runtime, None)
        await backend.close()
        await rt.shutdown()


@pytest.mark.anyio
async def test_terminal_computer_new_resume_and_modes(home: Path, tmp_path: Path) -> None:
    rt = runtime(tmp_path, Scripted([["好"]]))
    backend = EmbeddedBackend(rt)
    console = Console(file=io.StringIO(), record=True, width=140, color_system=None)
    try:
        rt.create(str(home), mode="auto")  # 同一路径的项目会话不应被电脑续接选中
        info, resumed = await open_session(backend, parse_args(["--computer"]), console)
        assert info["kind"] == "computer" and info["cwd"] == str(home) and not resumed
        again, resumed = await open_session(backend, parse_args(["--computer", "-c"]), console)
        assert resumed and again["id"] == info["id"]
        with create_pipe_input() as inp:
            term = Terminal(backend, console, info, inp=inp, output=DummyOutput())
            await term.command("/mode auto")
            assert term.info["mode"] == "ask" and "auto（" not in console.export_text(clear=True)
            await term.command("/mode")
            assert term.info["mode"] == "read_only"
            await term.command("/mode")
            assert term.info["mode"] == "ask"
            await term.command("/new")
            assert term.info["kind"] == "computer" and term.info["id"] != info["id"]
            await term.command("/help")
            assert "auto；" not in console.export_text(clear=True)
        assert next_mode("ask", computer=True) == "read_only"
        assert next_mode("read_only", computer=True) == "ask"
        for args in (["--computer", "--mode", "auto"], ["--computer", "--branch"]):
            with pytest.raises(SystemExit):
                parse_args(args)
    finally:
        await backend.close()


@pytest.mark.anyio
async def test_computer_headless_confirmation_hint(home: Path, tmp_path: Path) -> None:
    model = Scripted([[call("write_file", path="no.txt", content="不能写")], ["停下"]])
    backend = EmbeddedBackend(runtime(tmp_path, model))
    info = await backend.create(None, "ask", kind="computer")
    console = Console(file=io.StringIO(), record=True, color_system=None)
    try:
        await headless(backend, info["id"], "写一个文件", err=console, computer=True)
        output = console.export_text()
        assert "用交互模式来确认" in output and "--mode auto" not in output
        assert not (home / "no.txt").exists()
    finally:
        await backend.close()
