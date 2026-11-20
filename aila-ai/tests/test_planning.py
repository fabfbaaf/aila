"""长任务和整体规划：出方案（只读）→ 你批准 → 一阶段一阶段做，该停就停，重启后接着做"""

import asyncio
from pathlib import Path

import pytest

from app.runtime.loop import AgentConfig
from app.runtime.planning import MAX_CONTINUES, MAX_STAGES, parse_plan, plan_state
from app.runtime.runner import Runtime, RuntimeError_
from app.runtime.session import derive_messages

from .test_runtime import SHELL, Scripted, call

TWO = {"goal": "给购物车加打折", "stages": [
    {"title": "写打折函数", "goal": "cart.discount", "verify": "pytest tests/test_cart.py"},
    {"title": "接到结算", "goal": "结算时用上", "verify": "pytest"},
]}


def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "cart.py").write_text("def total(items):\n    return sum(items)\n", encoding="utf-8")
    return root


def runtime(tmp_path: Path, model: Scripted, max_steps: int = 60) -> Runtime:
    return Runtime(tmp_path / "data", lambda: model, shell=SHELL, config=AgentConfig(max_steps=max_steps))


async def propose(rt: Runtime, sid: str, plan: dict = TWO) -> list[dict]:
    return [e async for e in rt.run(sid, plan["goal"], plan=True)]


# ---------- 计划本身 ----------


def test_parse_plan_validates_and_trims() -> None:
    plan = parse_plan({"goal": "  目标  ", "stages": [{"title": " 一 ", "goal": "g", "verify": ""}]})
    assert plan == {"goal": "目标", "stages": [{"title": "一", "goal": "g", "verify": "（没写）"}]}
    for bad, msg in [({"stages": [{"title": "a"}]}, "goal"), ({"goal": "g", "stages": []}, "stages"),
                     ({"goal": "g", "stages": [{"goal": "x"}]}, "title"),
                     ({"goal": "g", "stages": [{"title": str(i)} for i in range(MAX_STAGES + 1)]}, "太多")]:
        with pytest.raises(ValueError, match=msg):
            parse_plan(bad)


@pytest.mark.anyio
async def test_plan_request_is_read_only_and_waits_for_you(tmp_path: Path) -> None:
    root = project(tmp_path)
    model = Scripted([[call("write_file", path="x.py", content="1")], [call("propose_plan", **TWO)], ["这样分两步。"]])
    rt = runtime(tmp_path, model)
    sid = rt.create(str(root), "auto").id
    events = await propose(rt, sid)
    assert next(e for e in events if e["type"] == "tool" and e["name"] == "write_file")["status"] == "denied"
    assert not (root / "x.py").exists() and rt.agent(sid).policy.mode == "auto"
    state = plan_state(rt.agent(sid).session.events)
    assert state and state.decision == "proposed" and [s.title for s in state.stages] == ["写打折函数", "接到结算"]
    assert rt.agent(sid).session.title == "出方案：给购物车加打折"
    assert "<改动清单" not in model.requests[0][0][-1]["content"] and "给购物车加打折" in model.requests[0][0][-1]["content"]
    names = [t["function"]["name"] for t in model.requests[0][1]]
    assert names.index("propose_plan") < names.index("stage_done") < names.index("stage_blocked") < names.index("remember")
    assert not rt.running(sid)  # 交完计划就停，等你批准
    await rt.shutdown()


# ---------- 按计划做 ----------


@pytest.mark.anyio
async def test_approve_drives_all_stages_in_one_stream(tmp_path: Path) -> None:
    root = project(tmp_path)
    model = Scripted([
        [call("propose_plan", **TWO)], ["计划交了。"],
        [call("stage_done", summary="写好了", verified="3 passed")], ["第一阶段好了"],
        [call("stage_done", summary="接好了", verified="全过")], ["都做完了"],
    ])
    rt = runtime(tmp_path, model)
    sid = rt.create(str(root), "ask").id
    await propose(rt, sid)
    rt.decide_plan(sid, True)
    events = [e async for e in rt.drive_plan(sid)]  # 一条流一直到计划做完
    starts = [e for e in events if e["type"] == "user"]
    assert [e["plan_stage"] for e in starts] == [0, 1]
    assert starts[0]["title"] == "第 1/2 阶段：写打折函数" and "pytest tests/test_cart.py" in starts[0]["text"]
    assert "已经做完的阶段：1. 写打折函数" in starts[1]["text"]
    assert events[-1]["type"] == "plan_end" and not rt.running(sid)
    state = plan_state(rt.agent(sid).session.events)
    assert state.finished and [s.summary for s in state.stages] == ["写好了（验收：3 passed）", "接好了（验收：全过）"]
    summary = rt.agent(sid).session.summary()["plan"]
    assert summary == {"decision": "approved", "stages": 2, "done": 2, "finished": True}
    # 日志推出来的消息是合法的；plan 事件不进模型消息
    messages = derive_messages("sys", rt.agent(sid).session.events)
    assert all(m["role"] in ("system", "user", "assistant", "tool") for m in messages)
    with pytest.raises(RuntimeError_, match="做完了"):
        rt.drive_plan(sid)
    await rt.shutdown()


@pytest.mark.anyio
async def test_keeps_going_past_step_limit_then_stops(tmp_path: Path) -> None:
    root = project(tmp_path)
    one = {"goal": "大活", "stages": [{"title": "很长的一步", "goal": "g", "verify": "v"}]}
    model = Scripted([[call("propose_plan", **one)], ["交了"], [call("read_file", path="cart.py")]])  # 之后一直读文件
    rt = runtime(tmp_path, model, max_steps=1)
    sid = rt.create(str(root), "ask").id
    [e async for e in rt.run(sid, "大活", plan=True)]
    rt.decide_plan(sid, True)
    events = [e async for e in rt.drive_plan(sid)]
    starts = [e for e in events if e["type"] == "user"]
    assert len(starts) == 1 + MAX_CONTINUES  # 第一次 + 自动接着做的次数
    # 第一轮剧本只回了一句话（没标完成），之后每轮都走到步数上限
    assert "上一轮结束时还没调用 stage_done" in starts[1]["text"] and starts[1]["title"].startswith("接着做第 1 阶段")
    assert "上一轮走到步数上限停了" in starts[2]["text"]
    assert events[-1]["type"] == "plan_pause" and events[-1]["reason"] == "limit"
    await rt.shutdown()


@pytest.mark.anyio
async def test_blocked_stage_pauses_and_resume_restarts_it(tmp_path: Path) -> None:
    root = project(tmp_path)
    model = Scripted([
        [call("propose_plan", **TWO)], ["交了"],
        [call("stage_blocked", reason="不知道打几折，要你定")], ["我卡住了"],
        [call("stage_done", summary="按九折做了", verified="过了")], ["好了"],
        [call("stage_done", summary="接好了", verified="过了")], ["全做完了"],
    ])
    rt = runtime(tmp_path, model)
    sid = rt.create(str(root), "ask").id
    await propose(rt, sid)
    rt.decide_plan(sid, True)
    events = [e async for e in rt.drive_plan(sid)]
    assert events[-1]["type"] == "plan_pause" and events[-1]["reason"] == "blocked" and "打几折" in events[-1]["text"]
    assert plan_state(rt.agent(sid).session.events).stages[0].status == "blocked"
    # 你回答了以后接着做：同一阶段重新开始
    events = [e async for e in rt.drive_plan(sid)]
    assert [e.get("plan_stage") for e in events if e["type"] == "user"] == [0, 1]
    assert events[-1]["type"] == "plan_end"
    await rt.shutdown()


@pytest.mark.anyio
async def test_pause_each_stage(tmp_path: Path) -> None:
    root = project(tmp_path)
    model = Scripted([
        [call("propose_plan", **TWO)], ["交了"],
        [call("stage_done", summary="一", verified="过")], ["好"],
        [call("stage_done", summary="二", verified="过")], ["好"],
    ])
    rt = runtime(tmp_path, model)
    sid = rt.create(str(root), "ask").id
    await propose(rt, sid)
    rt.decide_plan(sid, True, pause_each=True)
    events = [e async for e in rt.drive_plan(sid)]
    assert events[-1]["type"] == "plan_pause" and events[-1]["reason"] == "pause_each" and events[-1]["stage"] == 1
    events = [e async for e in rt.drive_plan(sid)]
    assert [e.get("plan_stage") for e in events if e["type"] == "user"] == [1] and events[-1]["type"] == "plan_end"
    await rt.shutdown()


@pytest.mark.anyio
async def test_interrupt_stops_plan_and_it_resumes_after_restart(tmp_path: Path) -> None:
    root = project(tmp_path)
    model = Scripted([[call("propose_plan", **TWO)], ["交了"], ["慢"] * 40], delay=0.02)
    rt = runtime(tmp_path, model)
    sid = rt.create(str(root), "ask").id
    await propose(rt, sid)
    rt.decide_plan(sid, True)
    events = []
    async for e in rt.drive_plan(sid):
        events.append(e)
        if e["type"] == "user":
            await asyncio.sleep(0.05)
            assert rt.interrupt(sid)
    assert events[-1]["type"] == "plan_pause" and events[-1]["text"] == "你打断了"
    await rt.shutdown()

    # 服务重启：从日志接着做，这一阶段算「接着做」
    model2 = Scripted([[call("stage_done", summary="一", verified="过")], ["好"], [call("stage_done", summary="二", verified="过")], ["好"]])
    rt2 = runtime(tmp_path, model2)
    state = plan_state(rt2.agent(sid).session.events)
    assert state.current == 0 and state.stages[0].status == "running"
    events = [e async for e in rt2.drive_plan(sid)]
    starts = [e for e in events if e["type"] == "user"]
    assert "上次停下了" in starts[0]["text"] and events[-1]["type"] == "plan_end"
    await rt2.shutdown()


@pytest.mark.anyio
async def test_decisions_and_guards(tmp_path: Path) -> None:
    root = project(tmp_path)
    model = Scripted([[call("stage_done", summary="x", verified="y")], ["好"], [call("propose_plan", **TWO)], ["交了"]])
    rt = runtime(tmp_path, model)
    sid = rt.create(str(root), "ask").id
    # 没有计划时：stage_done 只是告诉她不用调用；决定、推进都不行
    events = [e async for e in rt.run(sid, "随便做点")]
    assert next(e for e in events if e["type"] == "tool")["status"] == "error"
    with pytest.raises(RuntimeError_, match="没有等你决定"):
        rt.decide_plan(sid, True)
    with pytest.raises(RuntimeError_, match="没有批准过"):
        rt.drive_plan(sid)
    with pytest.raises(RuntimeError_, match="要做什么"):
        rt.run(sid, "  ", plan=True)
    await propose(rt, sid)
    rt.decide_plan(sid, False)
    assert plan_state(rt.agent(sid).session.events).decision == "rejected"
    with pytest.raises(RuntimeError_):
        rt.decide_plan(sid, True)  # 已经决定过了
    with pytest.raises(RuntimeError_, match="没有批准过"):
        rt.drive_plan(sid)
    await rt.shutdown()


# ---------- 接口、终端 ----------


@pytest.mark.anyio
async def test_api_plan_flow(tmp_path: Path) -> None:
    import json

    import httpx

    from app.deps import get_runtime
    from app.main import app

    root = project(tmp_path)
    model = Scripted([
        [call("propose_plan", **TWO)], ["交了"],
        [call("stage_done", summary="一", verified="过")], ["好"],
        [call("stage_done", summary="二", verified="过")], ["好"],
    ])
    rt = runtime(tmp_path, model)
    app.dependency_overrides[get_runtime] = lambda: rt
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver", timeout=30) as c:
            sid = (await c.post("/agent/sessions", json={"cwd": str(root), "mode": "ask"})).json()["id"]
            res = await c.post(f"/agent/sessions/{sid}/messages", json={"text": "给购物车加打折", "plan": True})
            events = [json.loads(line[5:]) for line in res.text.splitlines() if line.startswith("data:")]
            assert events[0]["plan_request"] is True and any(e.get("plan") for e in events)
            res = await c.post(f"/agent/sessions/{sid}/plan/approve", json={"pause_each": False})
            events = [json.loads(line[5:]) for line in res.text.splitlines() if line.startswith("data:")]
            assert events[0]["type"] == "plan_decision" and events[-1]["type"] == "plan_end"
            assert (await c.post(f"/agent/sessions/{sid}/plan/continue")).status_code == 409
            assert (await c.post(f"/agent/sessions/{sid}/plan/reject")).status_code == 409
            summary = next(s for s in (await c.get("/agent/sessions")).json() if s["id"] == sid)
            assert summary["plan"]["finished"] and summary["title"] == "出方案：给购物车加打折"
    finally:
        app.dependency_overrides.pop(get_runtime, None)
        await rt.shutdown()


@pytest.mark.anyio
async def test_terminal_plan_commands(tmp_path: Path) -> None:
    import io

    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    from rich.console import Console

    from app.cli.app import Terminal
    from app.cli.backend import EmbeddedBackend

    root = project(tmp_path)
    model = Scripted([
        [call("propose_plan", **TWO)], ["交了"],
        [call("stage_done", summary="一", verified="过")], ["好"],
        [call("stage_done", summary="二", verified="过")], ["好"],
    ])
    rt = runtime(tmp_path, model)
    backend = EmbeddedBackend(rt)
    console = Console(file=io.StringIO(), record=True, width=200, force_terminal=False, color_system=None)
    try:
        info = await backend.create(str(root), "ask")
        with create_pipe_input() as inp:
            term = Terminal(backend, console, info, inp=inp, output=DummyOutput())
            await term.command("/plan")
            assert "还没有计划" in console.export_text(clear=True)
            await term.command("/plan 给购物车加打折")
            assert "/plan go 照这个做" in console.export_text(clear=True)
            await term.command("/plan")
            out = console.export_text(clear=True)
            assert "等你决定" in out and "[ ] 1. 写打折函数" in out
            await term.command("/plan go")
            assert "计划全部做完了" in console.export_text(clear=True)
            await term.command("/plan")
            assert "[x] 2. 接到结算" in console.export_text(clear=True)
    finally:
        await backend.close()
        await rt.shutdown()


@pytest.mark.anyio
async def test_mock_model_can_demo_a_plan(tmp_path: Path) -> None:
    """没填密钥时用的假模型也能把长任务走一遍（界面演示用）"""
    from app.runtime.mock import MockCoderModel

    root = project(tmp_path)
    (root / "README.md").write_text("# 小商店\n", encoding="utf-8")
    model = MockCoderModel(delay=0)
    rt = Runtime(tmp_path / "data", lambda: model, shell=SHELL)
    sid = rt.create(str(root), "ask").id
    [e async for e in rt.run(sid, "给购物车加打折", plan=True)]
    state = plan_state(rt.agent(sid).session.events)
    assert state and state.goal == "给购物车加打折" and len(state.stages) == 2
    rt.decide_plan(sid, True)
    events = [e async for e in rt.drive_plan(sid)]
    assert events[-1]["type"] == "plan_end"
    await rt.shutdown()


@pytest.mark.anyio
async def test_waiting_marks_for_inbox(tmp_path: Path) -> None:
    """「等你」收件箱：等你同意、等你看计划、计划停了，各标一下；做完了就不标"""
    root = project(tmp_path)
    model = Scripted([
        [call("propose_plan", **TWO)], ["交了"],
        [call("stage_blocked", reason="要你定折扣")], ["卡住了"],
        [call("write_file", path="x.py", content="1")], ["写好了"],
    ])
    rt = runtime(tmp_path, model)
    sid = rt.create(str(root), "ask").id
    session = rt.agent(sid).session
    assert session.waiting() is None
    await propose(rt, sid)
    assert session.summary()["waiting"] == "plan"
    rt.decide_plan(sid, True)
    [e async for e in rt.drive_plan(sid)]
    assert session.waiting() == "paused"
    # 有个改文件的操作在等你同意
    async for e in rt.run(sid, "先随便写个文件"):
        if e["type"] == "confirm":
            assert session.waiting() == "confirm"
            rt.confirms.answer(e["id"], False)
    assert session.waiting() == "paused"  # 你回答了，回到计划停着的状态
    await rt.shutdown()


# ---------- 只读时别原地打转 ----------


@pytest.mark.anyio
async def test_plan_round_denials_say_what_to_do_and_stop_after_three(tmp_path: Path) -> None:
    root = project(tmp_path)
    # 模型一直想改文件：被挡三次就停下，不再走满步数
    model = Scripted([[call("write_file", path="x.py", content="1")]])
    rt = runtime(tmp_path, model)
    sid = rt.create(str(root), "ask").id
    events = await propose(rt, sid)
    denied = [e for e in events if e["type"] == "tool" and e.get("status") == "denied"]
    assert len(denied) == 3
    assert "propose_plan" in denied[0]["content"]  # 告诉她这一轮该怎么做
    assert next(e for e in events if e["type"] == "turn_end")["reason"] == "denied"
    assert len(model.requests) == 3
    assert rt.agent(sid).deny_hint == "" and rt.agent(sid).policy.mode == "ask"  # 这一轮结束，提示和模式都还原
    await rt.shutdown()


@pytest.mark.anyio
async def test_a_success_in_between_resets_the_denial_count(tmp_path: Path) -> None:
    root = project(tmp_path)
    model = Scripted([
        [call("write_file", path="a.py", content="1")],
        [call("write_file", path="b.py", content="1")],
        [call("read_file", path="cart.py")],
        [call("write_file", path="c.py", content="1")],
        [call("write_file", path="d.py", content="1")],
        ["算了，我先说说看法。"],
    ])
    rt = runtime(tmp_path, model)
    sid = rt.create(str(root), "read_only").id
    events = [e async for e in rt.run(sid, "改一下")]
    assert next(e for e in events if e["type"] == "turn_end")["reason"] == "done"
    await rt.shutdown()


@pytest.mark.anyio
async def test_approving_in_read_only_mode_is_refused(tmp_path: Path) -> None:
    root = project(tmp_path)
    model = Scripted([[call("propose_plan", **TWO)], ["计划交了。"]])
    rt = runtime(tmp_path, model)
    sid = rt.create(str(root), "read_only").id
    await propose(rt, sid)
    with pytest.raises(RuntimeError_, match="只读模式"):
        rt.decide_plan(sid, True)
    rt.set_mode(sid, "ask")
    assert rt.decide_plan(sid, True)["approved"] is True
    await rt.shutdown()
