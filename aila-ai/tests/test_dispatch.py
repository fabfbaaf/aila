"""跨 Agent 派工（runtime/dispatch.py）：主 Agent 用艾拉的工具把活派给另一个本机 Agent。
用真的 git 仓库验证开工作区、合并；派出去的 Agent 不真的跑（start 换成记一下），它的事件手动发。"""

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.deps import get_runtime
from app.main import app
from app.runtime import branch as branchmod
from app.runtime.dispatch import Dispatch, DispatchError
from app.runtime.external import ExternalAgent, _own_tool
from app.runtime.runner import Runtime

from .test_fix import make_repo, pytest_cmd
from .test_runtime import SHELL, Scripted

CALC_FIXED = "    return a + b"


@pytest.fixture(autouse=True)
def tests_with_this_python(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(branchmod, "detect_tests", lambda repo, files: pytest_cmd())


def setup(tmp_path: Path, mode: str = "auto") -> tuple[Runtime, str, list]:
    repo = make_repo(tmp_path / "proj")
    rt = Runtime(tmp_path / "data", lambda: Scripted([["好"]]), shell=SHELL, bridge_url="http://127.0.0.1:9999")
    started: list = []
    rt.start = lambda sid, text, **kw: started.append((sid, text))  # type: ignore[method-assign]
    parent = rt.create(str(repo), mode, engine="claude").id
    return rt, parent, started


def child_of(rt: Runtime, out: str) -> str:
    return json.loads(out)["id"]


def test_delegate_opens_a_worktree_session_and_tells_the_lead(tmp_path: Path) -> None:
    rt, parent, started = setup(tmp_path)
    out = asyncio.run(rt.dispatch.call(parent, "delegate", {"agent": "codex", "title": "后端", "task": "修好 add"}))
    cid = child_of(rt, out)
    child = rt.store.get(cid)
    assert child.events[0]["parent"] == parent and child.events[0]["engine"] == "codex"
    assert child.branch and Path(child.branch["worktree"]).is_dir()  # 独立工作区
    assert child.title.startswith("后端")
    assert started == [(cid, "修好 add")]
    notes = [e["text"] for e in rt.store.get(parent).events if e["type"] == "note"]
    assert any("派给 Codex" in n and "后端" in n for n in notes)
    assert "还没开始" in rt.dispatch.status(child)
    with pytest.raises(DispatchError):
        asyncio.run(rt.dispatch.call(parent, "delegate", {"agent": "gemini", "title": "x", "task": "y"}))
    # 别的任务派出去的管不着
    other = rt.create(str(tmp_path / "proj"), "auto", engine="codex").id
    with pytest.raises(DispatchError):
        asyncio.run(rt.dispatch.call(other, "work_status", {"id": cid}))


def test_helper_approvals_and_finish_show_up_in_the_lead_task(tmp_path: Path) -> None:
    rt, parent, _ = setup(tmp_path)
    cid = child_of(rt, asyncio.run(rt.dispatch.call(parent, "delegate", {"agent": "codex", "title": "后端", "task": "t"})))
    child = rt.store.get(cid)
    rt._publish(cid, child.append({"type": "confirm", "id": "c9", "call_id": "x1", "tool": "run_command", "kind": "command",
                                   "action": "运行命令 npm test", "detail": "npm test", "reason": "", "can_remember": True}))
    rt._publish(cid, child.append({"type": "confirm_end", "id": "c9", "result": "approved", "remember": False}))
    rt._publish(cid, child.append({"type": "turn_end", "reason": "done", "cost": None}))
    events = rt.store.get(parent).events
    confirm = next(e for e in events if e["type"] == "confirm")
    assert confirm["id"] == "c9" and confirm["call_id"] == f"sub-{cid}-x1"  # 在主任务里点同意，答的就是助手那边的
    assert confirm["action"].startswith("Codex（后端）：")
    assert any(e["type"] == "confirm_end" and e["id"] == "c9" for e in events)
    assert any(e["type"] == "note" and "做完了" in e["text"] for e in events)


def test_merge_runs_tests_and_shows_a_card_in_the_lead_task(tmp_path: Path) -> None:
    rt, parent, _ = setup(tmp_path, mode="auto")
    cid = child_of(rt, asyncio.run(rt.dispatch.call(parent, "delegate", {"agent": "codex", "title": "后端", "task": "t"})))
    wt = Path(rt.store.get(cid).branch["worktree"])
    calc = wt / "calc.py"
    calc.write_text(calc.read_text(encoding="utf-8").replace("    return a - b", CALC_FIXED), encoding="utf-8")
    assert "calc.py" in rt.dispatch.status(rt.store.get(cid))
    out = asyncio.run(rt.dispatch.call(parent, "merge_work", {"id": cid}))
    assert out.startswith("合并了") and "calc.py" in out
    assert CALC_FIXED in (tmp_path / "proj" / "calc.py").read_text(encoding="utf-8")
    card = next(e for e in rt.store.get(parent).events if e["type"] == "merge")
    assert card["merged"] and card["tests"][0]["ok"]


def test_merge_in_ask_mode_waits_for_the_user_without_blocking_the_lead(tmp_path: Path) -> None:
    rt, parent, _ = setup(tmp_path, mode="ask")

    async def go() -> None:
        cid = child_of(rt, await rt.dispatch.call(parent, "delegate", {"agent": "codex", "title": "后端", "task": "t"}))
        wt = Path(rt.store.get(cid).branch["worktree"])
        calc = wt / "calc.py"
        calc.write_text(calc.read_text(encoding="utf-8").replace("    return a - b", CALC_FIXED), encoding="utf-8")
        out = await rt.dispatch.call(parent, "merge_work", {"id": cid})
        assert "确认合并" in out  # 马上返回，不等你点
        await asyncio.sleep(0.05)
        confirm = next(e for e in rt.store.get(parent).events if e["type"] == "confirm")
        assert "+" + CALC_FIXED in confirm["detail"]  # diff 是这边生成的
        assert "在等用户" in await rt.dispatch.call(parent, "work_status", {"id": cid})
        rt.confirms.answer(confirm["id"], True)
        for _ in range(200):
            await asyncio.sleep(0.05)
            if any(e["type"] == "merge" for e in rt.store.get(parent).events):
                break
        assert "上次合并：合并了" in await rt.dispatch.call(parent, "work_status", {"id": cid})

    asyncio.run(go())
    assert CALC_FIXED in (tmp_path / "proj" / "calc.py").read_text(encoding="utf-8")


def test_discard_cleans_up(tmp_path: Path) -> None:
    rt, parent, _ = setup(tmp_path)
    cid = child_of(rt, asyncio.run(rt.dispatch.call(parent, "delegate", {"agent": "codex", "title": "后端", "task": "t"})))
    wt = Path(rt.store.get(cid).branch["worktree"])
    out = asyncio.run(rt.dispatch.call(parent, "discard_work", {"id": cid}))
    assert "放弃了" in out and not wt.exists()


def test_bridge_needs_the_session_token(tmp_path: Path) -> None:
    rt, parent, _ = setup(tmp_path)
    server = rt.dispatch.servers(parent)[0]
    assert server["url"] == "http://127.0.0.1:9999/agent/bridge"
    token = server["headers"][0]["value"]
    assert rt.dispatch.servers(parent)[0]["headers"][0]["value"] == token  # 同一个任务同一个令牌
    app.dependency_overrides[get_runtime] = lambda: rt
    try:
        client = TestClient(app)
        rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        assert client.post("/agent/bridge", json=rpc).status_code == 401
        assert client.post("/agent/bridge", json=rpc, headers={"Authorization": "Bearer nope"}).status_code == 401
        auth = {"Authorization": token}
        init = client.post("/agent/bridge", json={"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}, headers=auth).json()
        assert init["result"]["serverInfo"]["name"] == "aila"
        assert client.post("/agent/bridge", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=auth).status_code == 202
        names = [t["name"] for t in client.post("/agent/bridge", json=rpc, headers=auth).json()["result"]["tools"]]
        assert names == ["delegate", "work_status", "wait_work", "merge_work", "discard_work"]
        bad = client.post("/agent/bridge", json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                                 "params": {"name": "work_status", "arguments": {"id": "nope"}}}, headers=auth).json()
        assert bad["result"]["isError"]
    finally:
        app.dependency_overrides.pop(get_runtime, None)


def test_no_bridge_without_address(tmp_path: Path) -> None:
    rt = Runtime(tmp_path / "data", lambda: Scripted([["好"]]), shell=SHELL)
    assert Dispatch(rt, None).servers("x") == []


def test_own_tools_are_recognised_but_commands_are_not() -> None:
    assert _own_tool("mcp.aila.wait_work") and _own_tool("mcp__aila__delegate")
    assert not _own_tool("mcp.aila.rm_everything") and not _own_tool("mcp.other.delegate") and not _own_tool("rm -rf x")
    assert ExternalAgent  # 引用一下：桥接工具的放行在 test_external_agent 里测
