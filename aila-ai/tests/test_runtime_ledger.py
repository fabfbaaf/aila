"""执行轨迹要的计时，和上下文账本（上下文由哪几块组成、各占多少）"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.deps import get_runtime
from app.llm import ToolCall, Usage
from app.main import app
from app.runtime.runner import Runtime

from .test_runtime import SHELL, Scripted, drive, project  # noqa: F401（project 是 fixture）


def steps() -> list[list]:  # type: ignore[type-arg]
    return [
        [ToolCall("r", "read_file", json.dumps({"path": "README.md"})), Usage(3000, 20, 0, 3000)],
        [ToolCall("c", "run_command", json.dumps({"command": "git status"})), Usage(3300, 20, 3000, 300)],
        ["看完了。", Usage(3600, 10, 3300, 300)],
    ]


@pytest.mark.anyio
async def test_steps_and_tools_are_timed(project: Path, tmp_path: Path) -> None:
    rt = Runtime(tmp_path / "data", lambda: Scripted(steps(), delay=0.01), shell=SHELL)
    agent = rt.agent(rt.create(str(project)).id)
    events = await drive(agent, "看看项目")
    steps_ = [e for e in events if e["type"] == "assistant"]
    assert all(e["timing"]["total"] >= e["timing"]["first"] >= 0 for e in steps_)
    assert steps_[0]["timing"]["total"] > 0
    tools = [e for e in events if e["type"] == "tool"]
    assert all(t["seconds"] >= 0 for t in tools)
    # 计时只给界面看，不发给模型
    assert all("seconds" not in str(m.get("content")) or m["role"] != "tool" for m in agent.session.messages("S"))


@pytest.mark.anyio
async def test_context_ledger(project: Path, tmp_path: Path) -> None:
    rt = Runtime(tmp_path / "data", lambda: Scripted(steps()), shell=SHELL)
    session = rt.create(str(project))
    before = rt.context(session.id)
    assert before["estimated"] and [s["key"] for s in before["segments"]] == ["system", "tools"]

    await drive(rt.agent(session.id), "看看项目")
    ledger = rt.context(session.id)
    keys = [s["key"] for s in ledger["segments"]]
    assert keys[:4] == ["system", "tools", "context", "user"]  # 固定的在前
    assert "tool:read_file" in keys and "tool:run_command" in keys
    assert [s["fixed"] for s in ledger["segments"][:3]] == [True, True, True]
    assert not ledger["estimated"] and ledger["prompt"] == 3600 and ledger["cache_hit"] == 3300
    assert abs(sum(s["tokens"] for s in ledger["segments"]) - 3600) <= len(keys)  # 按真实输入等比例校准
    assert ledger["biggest"][0]["label"] in ("读文件 README.md", "命令 git status")
    assert ledger["window"] == 128_000 and ledger["compact_at"] == 0.8


def test_context_api(project: Path, tmp_path: Path) -> None:
    rt = Runtime(tmp_path / "data", lambda: Scripted([["好"]]), shell=SHELL)
    app.dependency_overrides[get_runtime] = lambda: rt
    try:
        client = TestClient(app)
        sid = client.post("/agent/sessions", json={"cwd": str(project)}).json()["id"]
        body = client.get(f"/agent/sessions/{sid}/context").json()
        assert body["segments"][0]["label"] == "系统提示词"
        assert client.get("/agent/sessions/20990101-000000-abcdef/context").status_code == 404
    finally:
        app.dependency_overrides.pop(get_runtime, None)
