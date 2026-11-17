"""撤销一轮的验收：用剧本模型实际读写文件，覆盖日志、工作区、终端和接口。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from hashlib import blake2b
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from app.cli.app import Terminal
from app.cli.backend import Backend, EmbeddedBackend, HttpBackend
from app.deps import get_runtime
from app.llm import StepItem, ToolCall
from app.main import app
from app.runtime.runner import Runtime, RuntimeError_
from app.runtime.session import Event

from .test_cli import recording
from .test_fix import make_repo, sh
from .test_runtime import SHELL, Scripted, call


def runtime(tmp_path: Path, *steps: list[StepItem]) -> Runtime:
    model = Scripted(list(steps))
    return Runtime(tmp_path / "data", lambda: model, shell=SHELL)


def edit(path: str, before: str, after: str) -> ToolCall:
    return call("edit_file", path=path, old_string=before, new_string=after)


def read(path: str) -> ToolCall:
    return call("read_file", path=path)


def write(path: str, content: str) -> ToolCall:
    return call("write_file", path=path, content=content)


async def run_turn(rt: Runtime, sid: str, text: str = "做这件事") -> list[Event]:
    events = [event async for event in rt.run(sid, text)]
    assert events[0]["type"] == "user"
    assert events[-1]["type"] == "turn_end" and events[-1]["reason"] == "done"
    return events


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    return root


@asynccontextmanager
async def http_backend(rt: Runtime) -> AsyncIterator[HttpBackend]:
    app.dependency_overrides[get_runtime] = lambda: rt
    backend = HttpBackend("http://testserver", transport=httpx.ASGITransport(app=app))
    try:
        yield backend
    finally:
        app.dependency_overrides.pop(get_runtime, None)
        await backend.close()


@pytest.mark.anyio
async def test_undo_restores_crlf_and_gbk_bytes_and_deletes_created_file(workspace: Path, tmp_path: Path) -> None:
    crlf = b"x = 1\r\ny = 2\r\n"
    gbk = "中文旧内容\r\n".encode("gbk")
    (workspace / "windows.txt").write_bytes(crlf)
    (workspace / "gbk.txt").write_bytes(gbk)
    rt = runtime(
        tmp_path,
        [read("windows.txt"), read("gbk.txt")],
        [edit("windows.txt", "y = 2", "y = 3"), edit("gbk.txt", "旧", "新"), write("new.txt", "新建")],
        ["改完了。"],
    )
    session = rt.create(str(workspace), "auto")
    turn = await run_turn(rt, session.id)
    before_log = session.path.read_bytes()
    assert (workspace / "windows.txt").read_bytes() != crlf
    assert (workspace / "gbk.txt").read_bytes() != gbk
    assert (workspace / "new.txt").is_file()

    # 重新打开会话后仍能找到原字节，历史日志本身只追加一行。
    await rt.shutdown()
    reopened = runtime(tmp_path, ["好。"])
    result = reopened.undo(session.id)
    assert result["type"] == "undo" and result["turn_seq"] == turn[0]["seq"]
    assert set(result["restored"]) == {"windows.txt", "gbk.txt", "new.txt"}
    assert result["skipped"] == {} and result["commands"] is False
    assert (workspace / "windows.txt").read_bytes() == crlf
    assert (workspace / "gbk.txt").read_bytes() == gbk
    assert not (workspace / "new.txt").exists()
    assert session.path.read_bytes().startswith(before_log)
    assert len(session.path.read_bytes()[len(before_log):].splitlines()) == 1
    await reopened.shutdown()


@pytest.mark.anyio
async def test_undo_uses_first_original_for_repeated_edits_and_preserves_empty_files(workspace: Path, tmp_path: Path) -> None:
    (workspace / "repeated.txt").write_bytes(b"0\r\n")
    (workspace / "empty.txt").touch()
    rt = runtime(
        tmp_path,
        [read("repeated.txt"), read("empty.txt")],
        [edit("repeated.txt", "0", "1"), write("empty.txt", "暂时有内容"), write("new.txt", "第一版")],
        [edit("repeated.txt", "1", "2"), edit("new.txt", "第一", "第二")],
        [edit("repeated.txt", "2", "3")],
        ["好了。"],
    )
    session = rt.create(str(workspace), "auto")
    await run_turn(rt, session.id)
    assert (workspace / "repeated.txt").read_bytes() == b"3\r\n"
    assert (workspace / "new.txt").read_text(encoding="utf-8") == "第二版"
    assert (workspace / "empty.txt").read_text(encoding="utf-8") == "暂时有内容"

    result = rt.undo(session.id)
    assert set(result["restored"]) == {"repeated.txt", "new.txt", "empty.txt"}
    assert result["skipped"] == {}
    assert (workspace / "repeated.txt").read_bytes() == b"0\r\n"
    assert (workspace / "empty.txt").read_bytes() == b""
    assert not (workspace / "new.txt").exists()
    await rt.shutdown()


@pytest.mark.anyio
async def test_undo_skips_after_user_edit_even_with_same_size(workspace: Path, tmp_path: Path) -> None:
    (workspace / "user.txt").write_bytes(b"A")
    (workspace / "restore.txt").write_bytes(b"A")
    rt = runtime(
        tmp_path,
        [read("user.txt"), read("restore.txt")],
        [edit("user.txt", "A", "B"), edit("restore.txt", "A", "B")],
        ["好了。"],
    )
    session = rt.create(str(workspace), "auto")
    await run_turn(rt, session.id)
    (workspace / "user.txt").write_bytes(b"C")

    result = rt.undo(session.id)
    assert result["restored"] == ["restore.txt"]
    assert set(result["skipped"]) == {"user.txt"} and "改" in result["skipped"]["user.txt"]
    assert (workspace / "user.txt").read_bytes() == b"C"
    assert (workspace / "restore.txt").read_bytes() == b"A"
    await rt.shutdown()


@pytest.mark.anyio
async def test_undo_only_latest_turn_and_allows_new_turn_afterwards(workspace: Path, tmp_path: Path) -> None:
    rt = runtime(
        tmp_path,
        [write("first.txt", "第一轮")], ["好了。"],
        [write("second.txt", "第二轮")], ["好了。"],
        [write("third.txt", "第三轮")], ["好了。"],
        ["只报告，没有改文件。"],
    )
    session = rt.create(str(workspace), "auto")
    await run_turn(rt, session.id, "第一轮")
    await run_turn(rt, session.id, "第二轮")
    assert rt.undo(session.id)["restored"] == ["second.txt"]
    assert not (workspace / "second.txt").exists()
    assert (workspace / "first.txt").read_text(encoding="utf-8") == "第一轮"
    with pytest.raises(RuntimeError_, match="撤销"):
        rt.undo(session.id)

    await run_turn(rt, session.id, "第三轮")
    assert rt.undo(session.id)["restored"] == ["third.txt"]
    assert not (workspace / "third.txt").exists()
    await run_turn(rt, session.id, "只报告")
    with pytest.raises(RuntimeError_, match="最近一轮没有改文件"):
        rt.undo(session.id)
    assert (workspace / "first.txt").exists()
    await rt.shutdown()


@pytest.mark.anyio
async def test_undo_preserves_model_prefix_and_requires_new_read(workspace: Path, tmp_path: Path) -> None:
    (workspace / "restore.txt").write_bytes(b"A")
    (workspace / "skip.txt").write_bytes(b"A")
    model = Scripted([
        [read("restore.txt"), read("skip.txt")],
        [edit("restore.txt", "A", "B"), edit("skip.txt", "A", "B")],
        ["好了。"],
        [edit("restore.txt", "A", "C")],
        ["需要重新读。"],
    ])
    rt = Runtime(tmp_path / "data", lambda: model, shell=SHELL)
    session = rt.create(str(workspace), "auto")
    agent = rt.agent(session.id)
    await run_turn(rt, session.id, "第一轮")
    previous = session.messages(agent.system)
    next_request = len(model.requests)
    (workspace / "skip.txt").write_bytes(b"C")

    rt.undo(session.id)
    assert session.messages(agent.system) == previous
    next_events = await run_turn(rt, session.id, "再试一次")
    messages, _ = model.requests[next_request]
    assert messages[:len(previous)] == previous
    next_user = messages[len(previous)]
    assert next_user["role"] == "user" and next_user["content"].endswith("再试一次")
    assert all(word in next_user["content"] for word in ("撤销", "restore.txt", "skip.txt"))
    edit_result = next(e for e in next_events if e["type"] == "tool" and e["name"] == "edit_file")
    assert edit_result["status"] == "error" and "还没读过" in edit_result["content"]
    assert (workspace / "restore.txt").read_bytes() == b"A"
    await rt.shutdown()


@pytest.mark.anyio
async def test_undo_note_reaches_next_user_message_even_after_compaction(workspace: Path, tmp_path: Path) -> None:
    rt = runtime(tmp_path, [write("temporary.txt", "新建")], ["好了。"], ["继续。"], ["下一件事。"])
    session = rt.create(str(workspace), "auto")
    await run_turn(rt, session.id, "先新建文件")
    rt.undo(session.id)
    session.append({"type": "compaction", "summary": "压缩前的摘要", "upto": len(session.events) + 1})
    await run_turn(rt, session.id, "接着做")
    await run_turn(rt, session.id, "再下一件事")

    messages = session.messages(rt.agent(session.id).system)
    continuing, following = [m["content"] for m in messages if m["role"] == "user"][-2:]
    assert continuing.endswith("接着做") and "撤销" in continuing and "temporary.txt" in continuing
    assert following == "再下一件事"
    await rt.shutdown()


@pytest.mark.anyio
@pytest.mark.parametrize("merged", [False, True])
async def test_undo_only_writes_git_worktree(tmp_path: Path, merged: bool) -> None:
    repo = make_repo(tmp_path / "repo")
    output = repo / "calc.py"
    original = output.read_bytes()
    rt = runtime(tmp_path, [read("calc.py"), edit("calc.py", "a - b", "a + b")], ["好了。"])
    session = rt.create(str(repo), "auto", isolate=True)
    assert session.branch
    worktree = session.workdir
    await run_turn(rt, session.id)
    assert b"a + b" in (worktree / "calc.py").read_bytes()
    assert output.read_bytes() == original
    if merged:
        result = await rt.merge(session.id, run_tests=False)
        assert result["merged"] and b"a + b" in output.read_bytes()
    expected = output.read_bytes()

    assert rt.undo(session.id)["restored"] == ["calc.py"]
    assert (worktree / "calc.py").read_bytes() == original
    assert output.read_bytes() == expected
    assert sh(repo, "branch", "--show-current").strip() == "main"
    await rt.discard(session.id)
    await rt.shutdown()


@pytest.mark.anyio
async def test_undo_never_falls_back_after_worktree_is_discarded(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    original = (repo / "calc.py").read_bytes()
    rt = runtime(tmp_path, [read("calc.py"), edit("calc.py", "a - b", "a + b")], ["好了。"])
    session = rt.create(str(repo), "auto", isolate=True)
    await run_turn(rt, session.id)
    await rt.discard(session.id)
    with pytest.raises(RuntimeError_, match="分支|工作区|目录"):
        rt.undo(session.id)
    assert (repo / "calc.py").read_bytes() == original
    await rt.shutdown()


@pytest.mark.anyio
async def test_undo_rejected_while_agent_is_doing_another_turn(workspace: Path, tmp_path: Path) -> None:
    rt = runtime(tmp_path, [write("current.txt", "文件")], ["好了。"])
    session = rt.create(str(workspace), "auto")
    await run_turn(rt, session.id)
    rt.start(session.id, "再做一件事")
    try:
        with pytest.raises(RuntimeError_, match="做事|结束|运行"):
            rt.undo(session.id)
        assert (workspace / "current.txt").read_text(encoding="utf-8") == "文件"
    finally:
        await rt.shutdown()


@pytest.mark.anyio
async def test_undo_safely_skips_old_log_and_path_outside_workspace(workspace: Path, tmp_path: Path) -> None:
    rt = runtime(tmp_path, ["好。"])
    session = rt.create(str(workspace), "auto")
    (workspace / "old.txt").write_bytes(b"old saved edit")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"must keep")
    session.append({"type": "user", "text": "以前改过"})
    session.append({"type": "tool", "name": "edit_file", "status": "ok", "file": "old.txt"})
    session.append({
        "type": "tool", "name": "write_file", "status": "ok", "file": "../outside.txt",
        "created": True, "after": blake2b(outside.read_bytes(), digest_size=16).hexdigest(),
    })
    session.append({"type": "turn_end", "reason": "done"})

    result = rt.undo(session.id)
    assert result["restored"] == []
    assert set(result["skipped"]) == {"old.txt", "../outside.txt"}
    assert (workspace / "old.txt").read_bytes() == b"old saved edit"
    assert outside.read_bytes() == b"must keep"
    await rt.shutdown()


@pytest.mark.anyio
async def test_delete_session_removes_snapshots(workspace: Path, tmp_path: Path) -> None:
    (workspace / "snapshot.txt").write_bytes(b"A")
    rt = runtime(tmp_path, [read("snapshot.txt"), edit("snapshot.txt", "A", "B")], ["好了。"])
    session = rt.create(str(workspace), "auto")
    await run_turn(rt, session.id)
    snapshot_dir = session.path.with_suffix(".snap")
    assert len(list(snapshot_dir.glob("*.bin"))) == 1

    await rt.delete(session.id)
    assert not snapshot_dir.exists()
    assert (workspace / "snapshot.txt").read_bytes() == b"B"
    await rt.shutdown()


@pytest.mark.anyio
async def test_undo_http_interface_rejects_stale_confirmation_and_remote_origin(workspace: Path, tmp_path: Path) -> None:
    rt = runtime(
        tmp_path, [write("first.txt", "第一轮")], ["好了。"], [write("second.txt", "第二轮")], ["好了。"],
    )
    session = rt.create(str(workspace), "auto")
    first, second = await run_turn(rt, session.id), await run_turn(rt, session.id)
    url = f"/agent/sessions/{session.id}/undo"

    async with http_backend(rt) as backend:
        client = backend.client
        stale = await client.post(url, json={"turn_seq": first[0]["seq"]})
        assert stale.status_code == 409
        assert (await client.post(url, json={"turn_seq": str(second[0]["seq"])})).status_code == 422
        assert (await client.post(url, headers={"Origin": "https://evil.example"})).status_code == 403
        assert (await client.post("/agent/sessions/20990101-000000-abcdef/undo")).status_code == 404
        assert (workspace / "second.txt").exists()
        response = await client.post(url)
        assert response.status_code == 200
        result = response.json()
        assert result["type"] == "undo" and result["turn_seq"] == second[0]["seq"]
        assert result["restored"] == ["second.txt"] and not (workspace / "second.txt").exists()
        assert (await client.post(url)).status_code == 409
    assert (workspace / "first.txt").exists()
    await rt.shutdown()


@pytest.mark.anyio
@pytest.mark.parametrize("remote", [False, True])
async def test_undo_cli_previews_confirms_and_lists_skips_and_command_effects(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, remote: bool,
) -> None:
    (workspace / "user.txt").write_bytes(b"A")
    rt = runtime(
        tmp_path, [read("user.txt")],
        [edit("user.txt", "A", "B"), write("rollback.txt", "待撤销"),
         call("run_command", command="echo keep > command.txt")],
        ["好了。"],
    )
    session = rt.create(str(workspace), "auto")
    events = await run_turn(rt, session.id)
    assert any(e["type"] == "tool" and e["name"] == "run_command" and e["status"] == "ok" for e in events)
    (workspace / "user.txt").write_bytes(b"C")

    async def interact(backend: Backend) -> None:
        info = await backend.session(session.id)
        with create_pipe_input() as inp:
            console = recording()
            term = Terminal(backend, console, info, inp=inp, output=DummyOutput())
            confirm = AsyncMock(side_effect=["n", "y"])
            monkeypatch.setattr(term.prompt, "prompt_async", confirm)
            await term.command("/undo")
            assert (workspace / "rollback.txt").exists()
            first_output = console.export_text()
            assert "准备撤销" in first_output and "rollback.txt" in first_output and "没撤销" in first_output

            await term.command("/undo")
            output = console.export_text()
            assert "退回 rollback.txt" in output and "跳过 user.txt" in output
            assert "命令做的事没有撤销" in output
            assert confirm.await_count == 2

    if remote:
        async with http_backend(rt) as backend:
            await interact(backend)
    else:
        await interact(EmbeddedBackend(rt))
    assert not (workspace / "rollback.txt").exists()
    assert (workspace / "user.txt").read_bytes() == b"C"
    assert (workspace / "command.txt").read_bytes().strip() == b"keep"
    await rt.shutdown()
