"""/review：只读地审一遍没提交的改动"""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from app.cli.commands import COMMANDS
from app.deps import get_runtime
from app.main import app
from app.runtime.review import ReviewError, review_request, review_title
from app.runtime.runner import Runtime, RuntimeError_
from app.runtime.session import derive_messages

from .test_fix import make_repo, sh
from .test_runtime import SHELL, Scripted, call


def runtime(tmp_path: Path, model: Scripted) -> Runtime:
    return Runtime(tmp_path / "data", lambda: model, shell=SHELL)


def test_request_lists_changes_generated_by_git(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "proj")
    text = review_request(repo)
    assert "notes.py" in text and "extra.py" in text  # 改过的和新建的都在清单里
    assert "1 file changed" in text  # diff 统计
    assert "只读" in text and "严重" in text
    assert "特别注意" not in text
    assert "这次特别注意：登录那块" in review_request(repo, "  登录那块 ")


def test_request_refuses_without_git_or_changes(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(ReviewError, match="不是 git 仓库"):
        review_request(plain)
    repo = make_repo(tmp_path / "proj")
    sh(repo, "-c", "core.autocrlf=false", "add", "-A")
    sh(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "all")
    with pytest.raises(ReviewError, match="没有还没提交的改动"):
        review_request(repo)


def test_request_works_before_first_commit(tmp_path: Path) -> None:
    repo = tmp_path / "fresh"
    repo.mkdir()
    sh(repo, "init", "-q")
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert "a.py" in review_request(repo)


@pytest.mark.anyio
async def test_review_turn_is_read_only_and_restores_mode(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "proj")
    model = Scripted([[call("write_file", path="hack.py", content="x = 1\n")], ["严重：没有。可以提交。"]])
    rt = runtime(tmp_path, model)
    sid = rt.create(str(repo), "auto").id
    events = [e async for e in rt.run(sid, "", review=True)]

    tool = next(e for e in events if e["type"] == "tool")
    assert tool["status"] == "denied" and "只读" in tool["content"]
    assert not (repo / "hack.py").exists()
    assert events[-1]["type"] == "turn_end" and events[-1]["reason"] == "done"
    # 这一轮结束后换回原来的模式；日志里没有记模式变化
    agent = rt.agent(sid)
    assert agent.policy.mode == "auto"
    assert not [e for e in agent.session.events if e["type"] == "mode"]

    user = next(e for e in agent.session.events if e["type"] == "user")
    assert user["review"] is True and user["title"] == review_title()
    assert agent.session.title == "审查没提交的改动"
    # 发给模型的是完整请求（带代码生成的改动清单），界面标记不进消息
    first = model.requests[0][0]
    assert "<改动清单" in first[-1]["content"] and "notes.py" in first[-1]["content"]
    messages = derive_messages("sys", agent.session.events)
    assert all("review" not in m for m in messages)


@pytest.mark.anyio
async def test_mode_change_during_review_waits_for_the_turn_to_end(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "proj")
    model = Scripted([[call("read_file", path="notes.py")], [call("write_file", path="late.py", content="1")], ["好了"]], delay=0.01)
    rt = runtime(tmp_path, model)
    sid = rt.create(str(repo), "ask").id
    seen = []
    async for e in rt.run(sid, "看看 notes", review=True):
        seen.append(e)
        if e["type"] == "tool" and e["name"] == "read_file":
            rt.set_mode(sid, "auto")
            assert rt.agent(sid).policy.mode == "read_only"  # 这一轮还是只读
    late = next(e for e in seen if e["type"] == "tool" and e["name"] == "write_file")
    assert late["status"] == "denied" and not (repo / "late.py").exists()
    assert rt.agent(sid).policy.mode == "auto"  # 结束后用你换的那个
    assert rt.agent(sid).session.title == "审查没提交的改动：看看 notes"


@pytest.mark.anyio
async def test_interrupted_review_restores_mode(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "proj")
    rt = runtime(tmp_path, Scripted([["慢"] * 50], delay=0.02))
    sid = rt.create(str(repo), "auto").id
    stream = rt.run(sid, "", review=True)
    async for e in stream:
        if e["type"] == "user":
            await asyncio.sleep(0.01)
            assert rt.interrupt(sid)
    assert rt.agent(sid).policy.mode == "auto"


def test_review_errors_before_starting(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    rt = runtime(tmp_path, Scripted([["好"]]))
    sid = rt.create(str(plain)).id
    with pytest.raises(RuntimeError_, match="不是 git 仓库"):
        rt.run(sid, "", review=True)
    assert not rt.running(sid)
    with pytest.raises(RuntimeError_, match="要做什么"):
        rt.run(sid, "  ")


@pytest.mark.anyio
async def test_api_review_flag(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "proj")
    plain = tmp_path / "plain"
    plain.mkdir()
    rt = runtime(tmp_path, Scripted([["没发现问题。"]]))
    app.dependency_overrides[get_runtime] = lambda: rt
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            sid = (await client.post("/agent/sessions", json={"cwd": str(repo), "mode": "ask"})).json()["id"]
            res = await client.post(f"/agent/sessions/{sid}/messages", json={"text": "", "review": True})
            assert res.status_code == 200
            events = [json.loads(line[5:]) for line in res.text.splitlines() if line.startswith("data:")]
            assert events[0]["type"] == "user" and events[0]["review"] is True
            assert events[-1]["type"] == "turn_end"
            # 不审的时候还是要有话
            assert (await client.post(f"/agent/sessions/{sid}/messages", json={"text": ""})).status_code == 422
            other = (await client.post("/agent/sessions", json={"cwd": str(plain), "mode": "ask"})).json()["id"]
            res = await client.post(f"/agent/sessions/{other}/messages", json={"review": True})
            assert res.status_code == 409 and "git" in res.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_runtime, None)


def test_terminal_lists_review_command() -> None:
    assert "/review" in COMMANDS
