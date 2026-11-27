"""用真的 Git 工作区和 pytest 验证两项派工共同验收，子 Agent 用剧本替代。"""

import asyncio
import json
from pathlib import Path
import sys

import pytest

from app.code.fix import TestCommand, git
from app.runtime import collaboration
from app.runtime.dispatch import DispatchError
from app.runtime.runner import Runtime, RuntimeError_

from .test_runtime import SHELL, Scripted, call


@pytest.fixture
def pair_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Runtime, str, Path, list[tuple[str, str]]]:
    repo = tmp_path / "project"
    (repo / "tests").mkdir(parents=True)
    (repo / "backend.py").write_text("def response():\n    return {'data': ['试验任务']}\n", encoding="utf-8")
    (repo / "frontend.py").write_text("def render(body):\n    return body.get('tasks', [])\n", encoding="utf-8")
    (repo / "tests/test_flow.py").write_text(
        "from backend import response\nfrom frontend import render\n\n"
        "def test_flow():\n    assert render(response()) == ['试验任务']\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text('[tool.pytest.ini_options]\npythonpath = ["."]\n', encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "-c", "user.name=Tests", "-c", "user.email=tests@example.com", "commit", "-qm", "baseline")
    monkeypatch.setattr(collaboration, "detect_tests", lambda *_: [
        TestCommand("", [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], "项目的 pytest", "python"),
    ])
    rt = Runtime(tmp_path / "data", lambda: Scripted([["好"]]), shell=SHELL, bridge_url="http://127.0.0.1:9999")
    started = []
    rt.start = lambda sid, message, **_: started.append((sid, message))  # type: ignore[method-assign]
    parent = rt.create(str(repo), "ask", isolate=True, engine="claude")
    (parent.workdir / "contract.txt").write_text("响应里的任务叫 items。", encoding="utf-8")
    return rt, parent.id, repo, started


def plan() -> dict:
    return {
        "contract": "接口返回 items，每项是一个任务名称。",
        "acceptance": "后端返回一条任务，前端显示这条任务。",
        "test_file": "tests/test_flow.py",
        "assignments": [
            {"agent": "claude", "title": "前端", "task": "显示任务", "paths": ["frontend.py"]},
            {"agent": "codex", "title": "后端", "task": "查询任务", "paths": ["backend.py"]},
        ],
    }


async def until_event(rt: Runtime, sid: str, kind: str, *, after: int = 0) -> dict:
    async with asyncio.timeout(25):
        while True:
            session = rt.store.get(sid)
            assert session is not None
            event = next((e for e in session.events[after:] if e["type"] == kind), None)
            if event:
                return event
            await asyncio.sleep(0.03)


async def deliver(rt: Runtime, parent: str, *, backend_key: str = "items") -> list[str]:
    session = rt.store.get(parent)
    assert session is not None
    await rt.dispatch.call(parent, "delegate_pair", plan())
    proposal = rt.dispatch.proposal(session)
    assert proposal is not None
    assert not any(e["type"] == "pair_plan" for e in session.events)
    picks = [{"agent": "claude", "model": ""}, {"agent": "codex", "model": "gpt-6.2"}]
    ids = json.loads(await rt.dispatch.decide_pair(session, proposal["proposal_id"], True, picks))["children"]
    for sid, filename, body in (
        (ids[0], "frontend.py", "def render(body):\n    return body.get('items', [])\n"),
        (ids[1], "backend.py", f"def response():\n    return {{'{backend_key}': ['试验任务']}}\n"),
    ):
        child = rt.store.get(sid)
        assert child is not None
        assert (child.workdir / "contract.txt").is_file()
        (child.workdir / filename).write_text(body, encoding="utf-8")
        child.append({"type": "turn_end", "reason": "done"})
    return ids


@pytest.mark.anyio
async def test_pair_validates_both_changes_then_confirms_once(pair_project) -> None:
    rt, pid, repo, started = pair_project
    parent = rt.store.get(pid)
    assert parent is not None and parent.branch is not None
    base = parent.branch["base"]
    try:
        front, back = await deliver(rt, pid)
        assert len(started) == 2 and all(plan()["contract"] in text for _, text in started)
        assert rt.store.get(front).branch["base"] == rt.store.get(back).branch["base"]
        assert any(e["type"] == "engine_initial_model" and e["value"] == "gpt-6.2" for e in rt.store.get(back).events)
        with pytest.raises(DispatchError, match="一起收回"):
            await rt.dispatch.call(pid, "merge_work", {"id": front})
        with pytest.raises(RuntimeError_, match="联合验收"):
            await rt.merge(back, run_tests=False)
        assert "正在临时工作区" in await rt.dispatch.call(pid, "validate_pair", {})
        async def follow():
            return [event async for event in rt.subscribe(pid, len(parent.events) - 1)]

        stream = asyncio.create_task(follow())
        confirmation = await until_event(rt, pid, "confirm")
        review = await until_event(rt, pid, "pair_review")
        assert review["detail"] == confirmation["detail"] and all(t["ok"] for t in review["tests"])
        assert "frontend.py" in confirmation["detail"] and "backend.py" in confirmation["detail"]
        assert "body.get('tasks'" in (parent.workdir / "frontend.py").read_text()
        rt.confirms.answer(confirmation["id"], True)
        report = await until_event(rt, pid, "merge")
        assert report["merged"] and len(report["tests"]) == 2 and all(t["ok"] for t in report["tests"])
        assert len([e for e in parent.events if e["type"] == "confirm"]) == 1
        assert parent.branch["base"] == base
        assert "body.get('items'" in (parent.workdir / "frontend.py").read_text()
        assert "return {'items'" in (parent.workdir / "backend.py").read_text(encoding="utf-8")
        assert "body.get('tasks'" in (repo / "frontend.py").read_text()
        events = await asyncio.wait_for(stream, timeout=5)
        assert "confirm" in [e["type"] for e in events] and "merge" in [e["type"] for e in events]
    finally:
        await rt.shutdown()


@pytest.mark.anyio
async def test_joint_test_failure_and_stale_confirmation_both_preserve_target(pair_project) -> None:
    rt, pid, _, _ = pair_project
    parent = rt.store.get(pid)
    assert parent is not None
    try:
        _, back = await deliver(rt, pid, backend_key="results")
        await rt.dispatch.call(pid, "validate_pair", {})
        failure = await until_event(rt, pid, "merge")
        assert not failure["merged"] and not failure["tests"][0]["ok"]
        assert "test_flow" in failure["tests"][0]["tail"]
        assert not any(e["type"] == "confirm" for e in parent.events)
        assert "验收工作区" in failure["reason"]
        child = rt.store.get(back)
        assert child is not None
        (child.workdir / "backend.py").write_text("def response():\n    return {'items': ['试验任务']}\n", encoding="utf-8")
        before = len(parent.events)
        await rt.dispatch.call(pid, "validate_pair", {})
        approval = await until_event(rt, pid, "confirm", after=before)
        (parent.workdir / "contract.txt").write_text("用户改了共同约定。", encoding="utf-8")
        rt.confirms.answer(approval["id"], True)
        stale = await until_event(rt, pid, "merge", after=before)
        assert not stale["merged"] and "主任务的代码已经变了" in stale["reason"]
        assert "body.get('tasks'" in (parent.workdir / "frontend.py").read_text()
        assert "用户改了" in (parent.workdir / "contract.txt").read_text(encoding="utf-8")
    finally:
        await rt.shutdown()


@pytest.mark.anyio
async def test_overlap_is_rejected_before_dispatch(pair_project) -> None:
    rt, pid, _, started = pair_project
    args = plan()
    args["assignments"][1]["paths"].append("frontend.py")
    try:
        with pytest.raises(DispatchError, match="负责范围重叠"):
            await rt.dispatch.call(pid, "delegate_pair", args)
        assert started == []
    finally:
        await rt.shutdown()


@pytest.mark.anyio
async def test_builtin_agent_proposes_work_without_starting_it(pair_project) -> None:
    rt, _, repo, started = pair_project
    session = rt.create(str(repo), "ask")
    try:
        tool = next(t for t in rt.dispatch.planning_tools(session.id) if t.name == "delegate_pair")
        assert (await tool.run(plan())).ok
        proposal = rt.dispatch.proposal(session)
        assert proposal is not None and not started
        await rt.dispatch.decide_pair(session, proposal["proposal_id"], False)
        assert rt.dispatch.proposal(session) is None and not started
    finally:
        await rt.shutdown()



@pytest.mark.anyio
async def test_lead_uses_preset_team_then_dispatches_three_workers(pair_project) -> None:
    rt, _, repo, started = pair_project
    picks = [
        {"agent": "codebuddy", "model": "hy4flash-free"},
        {"agent": "codex", "model": "gpt-6-sol"},
        {"agent": "claude", "model": ""},
    ]
    request = plan()
    request["assignments"].append({"title": "验收", "task": "补验收测试", "paths": ["tests/test_flow.py"]})
    model = Scripted([[call("delegate_pair", **request)], ["分工已提交。"]])
    rt.model_factory = lambda: model
    parent = rt.create(str(repo), "ask", collaborators=picks)
    try:
        [event async for event in rt.run(parent.id, "把任务列表跑通")]
        assert parent.summary()["collaborators"] == picks
        assert next(e for e in parent.events if e["type"] == "user")["text"] == "把任务列表跑通"
        prompt = str(model.requests[0][0][-1]["content"])
        assert "delegate_pair" in prompt and prompt.index("hy4flash-free") < prompt.index("gpt-6-sol")
        proposal = rt.dispatch.proposal(parent)
        assert proposal and proposal["preset"] and not started
        assert [{k: item[k] for k in ("agent", "model")} for item in proposal["assignments"]] == picks
        with pytest.raises(collaboration.CollaborationError, match="开任务时选好"):
            await rt.dispatch.decide_pair(parent, proposal["proposal_id"], True, list(reversed(picks)))
        assert not started
        ids = json.loads(await rt.dispatch.decide_pair(parent, proposal["proposal_id"], True))["children"]
        assert [sid for sid, _ in started] == ids
        children = [rt.store.get(sid) for sid in ids]
        assert [child.events[0]["engine"] for child in children] == [pick["agent"] for pick in picks]
        assert len({child.branch["worktree"] for child in children}) == 3
        assert len({child.branch["base"] for child in children}) == 1
        assert not rt.dispatch.opening_prompt(parent)
        changes = ["def render(body):\n    return body.get('items', [])\n",
                   "def response():\n    return {'items': ['试验任务']}\n",
                   (repo / "tests/test_flow.py").read_text(encoding="utf-8") + "\n# 验收覆盖联动\n"]
        for child, assignment, body in zip(children, request["assignments"], changes, strict=True):
            (child.workdir / assignment["paths"][0]).write_text(body, encoding="utf-8")
            child.append({"type": "turn_end", "reason": "done"})
        await rt.dispatch.call(parent.id, "validate_pair", {})
        approval = await until_event(rt, parent.id, "confirm")
        rt.confirms.answer(approval["id"], True)
        report = await until_event(rt, parent.id, "merge")
        assert report["merged"] and sorted(report["files"]) == ["backend.py", "frontend.py", "tests/test_flow.py"]
    finally:
        await rt.shutdown()
