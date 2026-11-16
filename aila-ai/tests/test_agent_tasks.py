"""从别处交给编程 Agent 的任务：/agent/tasks 接口、对话里的「编程任务」工具（开好后通知桌面端打开工作窗口）"""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agent import run_agent
from app.code import CodeProjects
from app.deps import get_runtime
from app.llm import MockModel
from app.main import app
from app.runtime.runner import Runtime
from app.tools import EMIT, Toolbox
from app.tools.coding import coding_skill

from .test_runtime import SHELL, Scripted


def make_runtime(tmp_path: Path, projects: CodeProjects | None = None) -> Runtime:
    return Runtime(tmp_path / "data", lambda: Scripted([["好的，看过了。"]]), shell=SHELL,
                   code_projects=(lambda: projects) if projects else None)


def folder(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.mkdir()
    (p / "README.md").write_text(f"# {name}\n", encoding="utf-8")
    return p


def test_tasks_api(tmp_path: Path) -> None:
    projects = CodeProjects(tmp_path / "data", lambda: None)
    shop = folder(tmp_path, "shop")
    pid = projects.add(str(shop)).id
    rt = make_runtime(tmp_path, projects)
    app.dependency_overrides[get_runtime] = lambda: rt
    try:
        client = TestClient(app)
        res = client.post("/agent/tasks", json={"text": "修好这个报错", "project": pid})
        assert res.status_code == 201 and res.json()["cwd"] == str(shop.resolve())
        events = client.get(f"/agent/sessions/{res.json()['id']}/events").text
        assert '"turn_end"' in events  # 后台做完了，订阅时补发得到
        assert client.post("/agent/tasks", json={"text": "x", "project": "nope"}).status_code == 400
        assert client.post("/agent/tasks", json={"text": "x"}).status_code == 400
        by_cwd = client.post("/agent/tasks", json={"text": "看看", "cwd": str(shop)})
        assert by_cwd.status_code == 201
    finally:
        app.dependency_overrides.pop(get_runtime, None)


async def run_tool(rt: Runtime, args: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    tool = coding_skill(lambda: rt).tools[0]
    side: list[dict[str, Any]] = []
    token = EMIT.set(side.append)
    try:
        return await tool.run(args), side
    finally:
        EMIT.reset(token)


@pytest.mark.anyio
async def test_coding_tool_finds_the_project(tmp_path: Path) -> None:
    projects = CodeProjects(tmp_path / "data", lambda: None)
    rt = make_runtime(tmp_path, projects)
    text, side = await run_tool(rt, {"task": "加个函数"})
    assert text.startswith("还不知道用户的项目在哪") and not side

    projects.add(str(folder(tmp_path, "shop")))
    projects.add(str(folder(tmp_path, "blog")))
    text, side = await run_tool(rt, {"task": "加个函数"})
    assert text.startswith("有好几个项目") and "shop" in text and "blog" in text and not side

    text, side = await run_tool(rt, {"task": "给购物车加打折", "project": "Shop"})
    assert text.startswith("已经在任务页里开始做了") and side[0]["type"] == "agent_task"
    sid = side[0]["session"]
    for _ in range(100):  # 等后台那一轮做完
        if not rt.running(sid):
            break
        await asyncio.sleep(0.02)
    session = rt.agent(sid).session
    assert session.title == "给购物车加打折" and session.events[-1]["type"] == "turn_end"


@pytest.mark.anyio
async def test_chat_passes_the_event_to_the_desktop(tmp_path: Path) -> None:
    projects = CodeProjects(tmp_path / "data", lambda: None)
    projects.add(str(folder(tmp_path, "shop")))
    rt = make_runtime(tmp_path, projects)
    toolbox = Toolbox(coding_skill(lambda: rt).tools)
    events = [e async for e in run_agent(MockModel(delay=0), [{"role": "user", "content": "在工作窗口里给购物车加个打折函数"}], toolbox)]
    task = next(e for e in events if e.get("type") == "agent_task")
    assert task["project"] == "shop"
    texts = "".join(str(e.get("text", "")) for e in events if e.get("type") == "text")
    assert "任务页" in texts
