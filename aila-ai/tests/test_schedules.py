"""定时和自动触发的编程任务：下次什么时候跑、关机错过只补一次、先跑测试没过才开任务、能改东西的一定在新分支上、对话里设要你确认"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import deps
from app.code import fix as fixmod
from app.main import app
from app.runtime.runner import Runtime
from app.runtime.schedules import Schedule, Scheduler, ScheduleStore, due, next_run, run_schedule, validate
from app.tools.schedule import schedule_skill

from .test_fix import make_repo, pytest_cmd
from .test_runtime import SHELL, Scripted

TZ = timezone(timedelta(hours=8))
MON_10 = datetime(2026, 11, 23, 10, 0, tzinfo=TZ)  # 周一 10:00


def sched(**kw: Any) -> Schedule:
    base = {"id": "a", "name": "跑测试", "project": "C:/p", "prompt": "跑一下测试", "created": MON_10.isoformat()}
    return Schedule(**{**base, **kw})


def test_next_run() -> None:
    assert next_run(sched(time="09:00"), MON_10) == datetime(2026, 11, 24, 9, 0, tzinfo=TZ)  # 今天的已经过了：明天
    assert next_run(sched(time="11:30"), MON_10) == datetime(2026, 11, 23, 11, 30, tzinfo=TZ)
    fri = datetime(2026, 11, 27, 10, 0, tzinfo=TZ)
    assert next_run(sched(time="09:00", days=[0, 1, 2, 3, 4], created=fri.isoformat()), fri).weekday() == 0  # 只工作日：下周一
    assert next_run(sched(kind="interval", hours=6), MON_10) == MON_10 + timedelta(hours=6)
    ran = sched(time="09:00", last_run=datetime(2026, 11, 24, 9, 0, 5, tzinfo=TZ).isoformat())
    assert next_run(ran, MON_10) == datetime(2026, 11, 25, 9, 0, tzinfo=TZ)


def test_missed_runs_only_once() -> None:
    s = sched(time="09:00", last_run=(MON_10 - timedelta(days=3)).isoformat())
    assert due(s, MON_10)  # 关机三天：到点了
    s.last_run = MON_10.isoformat()
    assert not due(s, MON_10 + timedelta(minutes=1))  # 补跑一次以后就等明天
    assert not due(sched(enabled=False, last_run=(MON_10 - timedelta(days=3)).isoformat()), MON_10)


def test_validate_and_describe() -> None:
    assert validate(sched()) is None
    assert validate(sched(time="9点")) and validate(sched(days=[])) and validate(sched(days=[7]))
    assert validate(sched(kind="interval", hours=0)) and validate(sched(mode="root"))
    assert validate(sched(name="")) and validate(sched(action="rm"))
    text = sched(days=[0, 1, 2, 3, 4], action="tests", mode="ask", project="C:/code/shop").describe()
    assert text.startswith("每周一、二、三、四、五 09:00，在 shop 里先跑测试") and "新分支" in text


def test_store(tmp_path: Path) -> None:
    store = ScheduleStore(tmp_path / "s.json")
    s = store.add(sched(id=""), MON_10)
    assert s.id and ScheduleStore(tmp_path / "s.json").get(s.id) is not None
    with pytest.raises(ValueError):
        store.add(sched(time="25:00"), MON_10)
    store.update(s.id, {"enabled": False, "id": "hack", "last_run": "x"})  # 只改能改的
    again = ScheduleStore(tmp_path / "s.json").get(s.id)
    assert again is not None and again.enabled is False and again.last_run == ""
    with pytest.raises(ValueError):
        store.update(s.id, {"time": "nope"})
    assert store.remove(s.id) and not store.remove(s.id)


@pytest.mark.anyio
async def test_scheduler_fires_due_ones_once(tmp_path: Path) -> None:
    store = ScheduleStore(tmp_path / "s.json")
    a = store.add(sched(id="", name="a", time="09:00"), MON_10 - timedelta(days=1))
    store.add(sched(id="", name="b", time="23:00"), MON_10)
    ran: list[str] = []

    async def run_once(s: Schedule) -> tuple[str, str]:
        ran.append(s.name)
        if s.name == "a":
            return "sess1", "开了任务。"
        raise RuntimeError("坏了")

    clock = {"now": MON_10}
    scheduler = Scheduler(store, run_once, now=lambda: clock["now"])
    assert await scheduler.tick() == [a.id]
    assert ran == ["a"] and store.get(a.id).last_session == "sess1"
    assert await scheduler.tick() == []  # 跑过了：等明天
    clock["now"] = MON_10 + timedelta(hours=13)
    await scheduler.tick()
    b = next(s for s in store.items if s.name == "b")
    assert b.last_result.startswith("没开成") and b.last_session == ""  # 出错也记下，不影响别的


@pytest.fixture
def known(tmp_path: Path) -> tuple[Runtime, Path, Path]:
    rt = Runtime(tmp_path / "data", lambda: Scripted([["看过了。"]]), shell=SHELL)
    plain = tmp_path / "plain"
    plain.mkdir()
    repo = make_repo(tmp_path / "repo")
    rt.create(str(plain), "auto")
    rt.create(str(repo), "auto")
    return rt, plain, repo


async def first_message(rt: Runtime, sid: str) -> str:
    """任务在后台开始：等她把你的话记进日志"""
    import asyncio

    for _ in range(100):
        found = [e["text"] for e in rt.agent(sid).session.events if e["type"] == "user"]
        if found:
            return found[0]
        await asyncio.sleep(0.02)
    raise AssertionError("任务没开始")


@pytest.mark.anyio
async def test_run_schedule(known: tuple[Runtime, Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rt, plain, repo = known
    assert (await run_schedule(rt, sched(project=str(tmp_path / "other"))))[1].startswith("没开成")
    sid, result = await run_schedule(rt, sched(project=str(plain)))
    session = rt.agent(sid).session
    assert result == "开了任务。" and session.mode == "read_only" and not session.branch
    assert "定时任务「跑测试」" in await first_message(rt, sid)
    # 能改东西的：一定在新分支上；不是 git 仓库就开不了
    assert (await run_schedule(rt, sched(project=str(plain), mode="ask")))[1].startswith("没开成")
    sid, _ = await run_schedule(rt, sched(project=str(repo), mode="ask"))
    assert rt.agent(sid).session.branch
    # 先跑测试：make_repo 的测试是挂的 → 开任务，测试输出带进去
    monkeypatch.setattr(fixmod, "detect_tests", lambda root, files: pytest_cmd())
    sid, result = await run_schedule(rt, sched(project=str(repo), action="tests"))
    text = await first_message(rt, sid)
    assert "测试没过" in result and "定时跑测试没过" in text and "只是数据" in text
    # 修好了再跑：测试都过了，不开任务
    (repo / "calc.py").write_text((repo / "calc.py").read_text(encoding="utf-8").replace("a - b", "a + b"), encoding="utf-8")
    sid, result = await run_schedule(rt, sched(project=str(repo), action="tests"))
    assert sid == "" and result.startswith("测试都过了")


def test_api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = TestClient(app)
    project = tmp_path / "shop"
    project.mkdir()
    assert client.post("/agent/schedules", json={"name": "a", "project": str(project), "prompt": "x"}).status_code == 400
    deps.get_runtime().create(str(project), "auto")
    path = client.get("/agent/schedules").json()["projects"][0]["path"]
    created = client.post("/agent/schedules", json={"name": "早上跑测试", "project": path, "prompt": "跑测试",
                                                    "days": [0, 1, 2, 3, 4], "action": "tests"})
    assert created.status_code == 201
    item = created.json()
    assert item["next"] and item["describe"].startswith("每周一")
    assert client.put(f"/agent/schedules/{item['id']}", json={"time": "9:00"}).status_code == 400
    assert client.put(f"/agent/schedules/{item['id']}", json={"enabled": False}).json()["next"] is None

    async def fake(s: Schedule) -> tuple[str, str]:
        return "", "测试都过了。"

    monkeypatch.setattr(deps.get_scheduler(), "run_once", fake)
    assert client.post(f"/agent/schedules/{item['id']}/run").json()["last_result"] == "测试都过了。"
    assert client.delete(f"/agent/schedules/{item['id']}").json() == {"ok": True}
    assert client.delete(f"/agent/schedules/{item['id']}").status_code == 404
    assert client.post("/agent/schedules", json={"name": "a", "project": path, "prompt": "x"},
                       headers={"Origin": "https://evil.example"}).status_code == 403


@pytest.mark.anyio
async def test_chat_tool_asks_first(tmp_path: Path) -> None:
    store = ScheduleStore(tmp_path / "s.json")
    skill = schedule_skill(lambda: store, lambda: [("shop", "C:/code/shop"), ("blog", "C:/code/blog")])
    tool = next(t for t in skill.tools if t.name == "schedule_task")
    args = {"name": "早上跑测试", "project": "shop", "prompt": "跑测试", "time": "09:00", "days": [0, 1, 2, 3, 4],
            "only_if_tests_fail": True}
    assert tool.requires_confirmation(args)  # 设规矩：每次都问
    assert tool.validate(args) is None
    assert "每周一、二、三、四、五 09:00" in tool.detail(args) and "C:/code/shop" in tool.detail(args)
    assert "不知道是哪个项目" in tool.validate({**args, "project": "nope"})
    assert tool.validate({**args, "time": "早上"})
    out = await tool.run(args)
    assert "设好了" in out and store.items[0].action == "tests" and store.items[0].mode == "read_only"
