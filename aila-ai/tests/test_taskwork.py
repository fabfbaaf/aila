"""任务管理通过真实会话日志、Git 工作区和假的 ACP 适配器验收。"""

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from acp import schema

from app.runtime import external
from app.runtime.localagents import LocalAgentError
from app.runtime.runner import Runtime, RuntimeError_
from app.runtime.taskwork import ModelPick, TaskWork

from .test_agent_tasks import folder, make_runtime
from .test_external_agent import FakeConn, Harness
from .test_fix import make_repo, sh


async def do_work(rt: Runtime, sid: str, text: str = "做一个改动") -> list[dict]:
    return [event async for event in rt.run(sid, text)]


@pytest.mark.anyio
async def test_queue_waits_for_acceptance_and_honors_project_limit(tmp_path: Path) -> None:
    rt = make_runtime(tmp_path)
    root = str(folder(tmp_path, "project"))
    project = rt.taskwork
    try:
        project.set_limit(root, 1)
        first = project.enqueue(root, "前置", "先做", [], ModelPick(), None, False)
        next_ = project.enqueue(root, "依赖", "再做", [first.id], ModelPick(), None, False)
        project.tick()
        assert first.state == "running"
        assert next_.state == "queued" and project.state()["queue"][1]["waiting"][0]["title"] == "前置"
        manual = rt.create(root)
        with pytest.raises(RuntimeError_, match="上限"):
            rt.start(manual.id, "也开始")
        await rt._tasks[first.sid]
        project.tick()
        assert first.state == "review" and next_.sid == ""  # 模型结束还没验收
        project.verdict(first.sid, False, "补上边界检查")
        before = rt.store.get(first.sid)
        assert before and before.summary()["acceptance"] == "rejected"
        assert project.state()["queue"][1]["waiting"][0]["status"] == "rejected"
        project.tick()
        assert next_.sid == ""
        await rt._tasks[first.sid]
        assert any(event["type"] == "user" and event.get("rejection") for event in before.events)
        assert next_.sid == ""
        rt.accept(first.sid, "accepted")
        project.tick()
        assert next_.state == "running"
        await rt._tasks[next_.sid]
        project.tick()
        restored = TaskWork(rt)
        assert restored.state()["queue"][0]["state"] == "accepted"
        recommended = restored.recommend(root)
        assert recommended["limit"] == 1
        assert recommended["models"][0]["rework"] == 1
    finally:
        await rt.shutdown()


@pytest.mark.anyio
async def test_queue_follows_successful_merges_and_cancelled_dependencies(tmp_path: Path) -> None:
    rt = make_runtime(tmp_path)
    root = str(folder(tmp_path, "project"))
    try:
        first = rt.create(root)
        await do_work(rt, first.id)
        blocked = rt.taskwork.enqueue(root, "合并后再做", "继续", [first.id], ModelPick(), None, False)
        dependent = rt.taskwork.enqueue(root, "之后", "再继续", [blocked.id], ModelPick(), None, False)
        rt.taskwork.tick()
        assert not blocked.sid
        first.append({"type": "merge", "merged": True})
        rt.taskwork.tick()
        assert blocked.sid and not dependent.sid
        pending = rt.taskwork.enqueue(root, "可取消", "还在等", [dependent.id], ModelPick(), None, False)
        final = rt.taskwork.enqueue(root, "最后", "继续", [pending.id], ModelPick(), None, False)
        rt.taskwork.cancel(pending.id)
        assert rt.taskwork.state()["queue"][-1]["id"] == final.id
        assert rt.taskwork.state()["queue"][-1]["waiting"][0]["status"] == "canceled"
    finally:
        await rt.shutdown()


@pytest.mark.anyio
@pytest.mark.parametrize("code", ["quota", "rate_limit", "auth"])
async def test_fallback_only_for_confirmed_quota(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: str) -> None:
    rt = make_runtime(tmp_path)
    root = str(folder(tmp_path, "project"))
    started: list[str] = []
    monkeypatch.setattr(rt, "start", lambda sid, text: started.append(sid))
    try:
        queued = rt.taskwork.enqueue(root, "用完额度换模型", "保留工作", [], ModelPick(), ModelPick("claude", "m-fast"), False)
        rt.taskwork.tick()
        old = rt.store.get(queued.sid)
        assert old is not None
        old.append({"type": "user", "text": "先改一点"})
        old.append({"type": "turn_end", "reason": "error", "error_code": code})
        rt.taskwork.tick()
        if code != "quota":
            assert queued.state == "stopped" and started == [old.id]
            return
        new = rt.store.get(queued.sid)
        assert new is not None and new.id != old.id and started == [old.id, new.id]
        assert new.cwd == old.cwd
        assert any(e["type"] == "handoff_in" and e["from"] == old.id for e in new.events)
        assert any(e["type"] == "engine_initial_model" and e["value"] == "m-fast" for e in new.events)
        new.append({"type": "turn_end", "reason": "error", "error_code": "quota"})
        rt.taskwork.tick()
        assert queued.state == "stopped" and len(started) == 2
    finally:
        await rt.shutdown()


@pytest.mark.anyio
async def test_recommendation_uses_real_verdict_and_the_model_that_ran(tmp_path: Path) -> None:
    rt = make_runtime(tmp_path)
    root = str(folder(tmp_path, "project"))
    try:
        good, bad, unreviewed = [rt.create(root) for _ in range(3)]
        for session, model in [(good, "m-a"), (bad, "m-b"), (unreviewed, "made-up")]:
            session.append({"type": "user", "text": "实现", "model": model})
            session.append({"type": "turn_end", "reason": "done"})
        good.append({"type": "user", "text": "修一下", "model": "m-a"})
        good.append({"type": "turn_end", "reason": "done"})
        rt.taskwork.verdict(good.id, True)
        bad.append({"type": "merge", "merged": False, "tests": [{"ok": False}]})
        rows = rt.taskwork.recommend(root)["models"]
        assert [(row["model"], row["samples"], row["passed"], row["rework"]) for row in rows] == [
            ("m-a", 1, 1, 1.0), ("m-b", 1, 0, 0.0)
        ]
        assert not rt.taskwork.recommend(str(folder(tmp_path, "other")))["models"]
    finally:
        await rt.shutdown()


@pytest.mark.anyio
async def test_group_keeps_per_repo_worktrees_and_shared_integration(tmp_path: Path) -> None:
    rt = make_runtime(tmp_path)
    left, right = make_repo(tmp_path / "front"), make_repo(tmp_path / "backend")
    try:
        group = rt.taskwork.create_group("跨仓库", [str(left), str(right)], "GET /count", "前后端都返回 200", ModelPick())
        first, second = group["members"]
        assert first["branch"] != second["branch"]
        front, back = Path(first["worktree"]), Path(second["worktree"])
        assert front.parent == back.parent
        assert sh(front, "rev-parse", "--abbrev-ref", "HEAD").strip() == first["branch"]
        assert sh(back, "rev-parse", "--abbrev-ref", "HEAD").strip() == second["branch"]
        (front / "front.txt").write_text("新文件", encoding="utf-8")
        assert not (left / "front.txt").exists()
        assert rt.taskwork.group_session(group["id"], first["sid"]) == first["sid"]
        await do_work(rt, first["sid"])
        first_session = rt.store.get(first["sid"])
        assert first_session and any("GET /count" in e.get("text", "") for e in first_session.events if e["type"] == "user")
        group_sid = rt.taskwork.group_session(group["id"], None)
        await do_work(rt, group_sid, "开始联调")
        loaded = TaskWork(rt)
        assert loaded.group_session(group["id"], None) == group_sid
        integration = rt.store.get(group_sid)
        assert integration is not None and integration.cwd == front.parent
        prompt = next(e["text"] for e in integration.events if e["type"] == "user")
        assert str(front) in prompt and str(back) in prompt and "前后端都返回 200" in prompt
        integration.append({"type": "user", "text": "跑联调检查"})
        integration.append({"type": "assistant", "tool_calls": [
            {"id": "t1", "name": "run_command", "arguments": '{"command":"git status"}'}
        ]})
        integration.append({"type": "tool", "id": "t1", "name": "run_command", "exit_code": 0})
        integration.append({"type": "turn_end", "reason": "done"})
        assert loaded.state()["groups"][0]["integration_result"]["commands"] == [
            {"id": "t1", "command": "git status", "exit_code": 0}
        ]
    finally:
        await rt.shutdown()


@pytest.mark.anyio
async def test_original_session_import_replays_once_and_preserves_native_id(tmp_path: Path) -> None:
    harness = Harness(tmp_path, "ask", [("say", "继续完成")])
    harness.session.append({"type": "native_import", "id": "resume-me"})
    harness.session.append({"type": "engine_session", "id": "resume-me"})
    await harness.agent.prepare()
    assert ("load", "resume-me") in harness.conns[-1].calls
    assert not any(c[0] in {"prompt", "new"} for c in harness.conns[-1].calls)
    assert [e["text"] for e in harness.session.events if e.get("imported")] == ["以前说过的"]
    await harness.agent.close()
    harness.agent = harness.make()
    await harness.turn("接着做")
    assert sum(bool(e.get("imported")) for e in harness.session.events) == 1
    assert ("load", "resume-me") in harness.conns[-1].calls
    harness.load = False
    harness.agent = harness.make()
    with pytest.raises(LocalAgentError, match="原会话"):
        await harness.agent.prepare()
    assert not any(c[0] == "new" for c in harness.conns[-1].calls)


@pytest.mark.anyio
async def test_native_discovery_checks_capabilities_before_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Discovery(FakeConn):
        supports_list = True
        async def initialize(self, **options):
            return SimpleNamespace(agent_capabilities=SimpleNamespace(
                load_session=True, session_capabilities=SimpleNamespace(list={} if self.supports_list else None)))

        async def list_sessions(self, cwd, cursor):
            assert cwd == str(tmp_path) and cursor == "page-2"
            return schema.ListSessionsResponse(sessions=[schema.SessionInfo(
                session_id="original", cwd=cwd, title="原会话")], next_cursor="page-3")

    @asynccontextmanager
    async def spawn(factory, *args, **kwargs):
        yield Discovery(factory, []), None

    monkeypatch.setattr(external, "launch", lambda *args: ("node", [], {}))
    monkeypatch.setattr(external.acp, "spawn_agent_process", spawn)
    page = await external.list_native("claude", tmp_path, tmp_path, "page-2")
    assert page["sessions"][0]["id"] == "original" and page["next_cursor"] == "page-3"
    Discovery.supports_list = False
    assert (await external.list_native("claude", tmp_path, tmp_path))["manual"]


@pytest.mark.anyio
async def test_native_adapter_with_resume_but_no_replay(tmp_path: Path) -> None:
    harness = Harness(tmp_path, "ask", [], load=False)
    harness.session.append({"type": "native_import", "id": "resume-me"})
    harness.session.append({"type": "engine_session", "id": "resume-me"})

    class ResumeOnly(FakeConn):
        async def initialize(self, **options):
            return schema.InitializeResponse(protocol_version=1, agent_capabilities=schema.AgentCapabilities(
                load_session=False, session_capabilities=schema.SessionCapabilities(resume=schema.SessionResumeCapabilities())))

        async def resume_session(self, session_id, cwd, **options):
            self.calls.append(("resume", session_id))
            return schema.ResumeSessionResponse(config_options=self.options())

    @asynccontextmanager
    async def spawn(factory, *args, **kwargs):
        conn = ResumeOnly(factory, [])
        harness.conns.append(conn)
        yield conn, None

    harness.agent._spawn = spawn
    await harness.agent.prepare()
    assert ("resume", "resume-me") in harness.conns[-1].calls
    assert not any(c[0] in {"prompt", "new", "load"} for c in harness.conns[-1].calls)
    assert not any(e.get("imported") for e in harness.session.events)
    await harness.agent.close()
