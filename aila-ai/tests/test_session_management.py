"""会话管理从日志到接口、双后端终端一起验证，确保删除不会留下进程或改写模型历史。"""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from app.cli.app import Terminal
from app.cli.backend import Backend, BackendError, EmbeddedBackend, HttpBackend
from app.deps import get_runtime
from app.main import app
from app.runtime import runner as runnermod
from app.runtime.branch import BranchError
from app.runtime.runner import Runtime, RuntimeError_
from app.runtime.session import SessionStore

from .test_cli import recording, wait_for
from .test_fix import make_repo, sh
from .test_runtime import SHELL, Scripted
from .test_runtime_jobs import script


def runtime(tmp_path: Path, model: Scripted | None = None) -> Runtime:
    return Runtime(tmp_path / "data", lambda: model or Scripted([["好"]]), shell=SHELL)


def test_rename_appends_and_reloads_without_changing_model_messages(tmp_path: Path) -> None:
    rt = runtime(tmp_path)
    session = rt.create(str(tmp_path))
    session.append({"type": "user", "text": "原来的标题\n细节"})
    messages = session.messages("系统提示")
    before = session.path.read_bytes()
    renamed = rt.rename(session.id, "  新标题  ")
    assert renamed.title == "新标题" and renamed.events[-1]["type"] == "title"
    assert session.path.read_bytes().startswith(before)
    assert renamed.messages("系统提示") == messages
    rt.rename(session.id, "长" * 100)
    loaded = SessionStore(rt.store.root).get(session.id)
    assert loaded is not None and loaded.title == "长" * 100
    assert loaded.messages("系统提示") == messages


@pytest.mark.parametrize("title", ["", "  \n ", "长" * 101])
def test_invalid_title_does_not_append(tmp_path: Path, title: str) -> None:
    rt = runtime(tmp_path)
    session = rt.create(str(tmp_path))
    before = session.path.read_bytes()
    with pytest.raises(RuntimeError_, match="1–100"):
        rt.rename(session.id, title)
    assert session.path.read_bytes() == before


def test_search_by_latest_title_or_path_and_case_insensitive_project(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    older = store.create(Path("C:/Code/Shop"), "ask", "m", "")
    older.append({"type": "user", "text": "最早的名字"})
    older.append({"type": "title", "title": "Needle 修购物车"})
    other = store.create(Path("D:/Code/Other"), "ask", "m", "")
    other.append({"type": "title", "title": "Needle 另一个项目"})
    for _ in range(52):
        store.create(Path("D:/Code/Other"), "ask", "m", "")
    assert [s["id"] for s in store.list(limit=1, query="nEeDlE", cwd="c:\\CODE\\SHOP\\")] == [older.id]
    assert {s["id"] for s in store.list(query="needle")} == {older.id, other.id}
    assert [s["id"] for s in store.list(query="sHoP")] == [older.id]
    assert store.list(query="最早的名字") == []
    assert store.list(cwd="C:/Code/Shop-extra") == []
    assert store.list(limit=0) == []


@pytest.mark.anyio
async def test_delete_removes_file_cache_and_idle_subscription(tmp_path: Path) -> None:
    rt = runtime(tmp_path)
    session = rt.create(str(tmp_path))
    stream = rt.subscribe(session.id)
    await rt.delete(session.id)
    assert not session.path.exists() and rt.store.list() == []
    assert rt.store.get(session.id) is None
    assert session.id not in rt._agents and session.id not in rt._tasks and session.id not in rt._listeners
    assert [e["type"] async for e in stream] == ["start"]
    with pytest.raises(RuntimeError_, match="没有这个会话"):
        rt.agent(session.id)
    with pytest.raises(RuntimeError_, match="没有这个会话"):
        await rt.delete(session.id)
    assert SessionStore(rt.store.root).get(session.id) is None


@pytest.mark.anyio
async def test_running_session_must_be_interrupted_before_delete(tmp_path: Path) -> None:
    rt = runtime(tmp_path, Scripted([["还在想"]], delay=60))
    session = rt.create(str(tmp_path))
    rt.start(session.id, "做点事")
    try:
        with pytest.raises(RuntimeError_, match="先打断"):
            await rt.delete(session.id)
        assert session.path.exists()
        assert rt.interrupt(session.id)
        await asyncio.gather(rt._tasks[session.id], return_exceptions=True)
        await rt.delete(session.id)
        assert not session.path.exists()
    finally:
        await rt.shutdown()


@pytest.mark.anyio
async def test_delete_stops_background_jobs_before_removing_session(tmp_path: Path) -> None:
    rt = runtime(tmp_path)
    session = rt.create(str(tmp_path))
    tools = rt.agent(session.id).tools
    command = script(tmp_path, "import time\nprint('ready', flush=True)\ntime.sleep(60)\n")
    try:
        result = await tools.tools["run_command"].run({"command": command, "background": True})
        assert result.ok
        job = tools.jobs.jobs[result.extra["job_id"]]
        assert job.proc.returncode is None
        await asyncio.wait_for(rt.delete(session.id), 10)
        assert job.proc.returncode is not None and job.task.done()
        assert not session.path.exists()
    finally:
        await rt.shutdown()


@pytest.mark.anyio
async def test_delete_prevents_new_turn_or_second_delete_while_cleaning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rt = runtime(tmp_path)
    session = rt.create(str(tmp_path))
    tools = rt.agent(session.id).tools
    entered, release = asyncio.Event(), asyncio.Event()

    async def shutdown() -> None:
        entered.set()
        await release.wait()

    monkeypatch.setattr(tools.jobs, "shutdown", shutdown)
    deleting = asyncio.create_task(rt.delete(session.id))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        with pytest.raises(RuntimeError_, match="正在删除"):
            rt.start(session.id, "不能重建日志")
        with pytest.raises(RuntimeError_, match="正在删除"):
            rt.rename(session.id, "不能重建日志")
        with pytest.raises(RuntimeError_, match="正在删除"):
            await rt.delete(session.id)
    finally:
        release.set()
        await deleting
        await rt.shutdown()
    assert not session.path.exists()


@pytest.mark.anyio
async def test_delete_waits_for_other_operations_that_write_session_log(tmp_path: Path) -> None:
    model = Scripted([["正在总结"]], delay=60)
    rt = runtime(tmp_path, model)
    session = rt.create(str(tmp_path))
    compacting = asyncio.create_task(rt.compact(session.id))
    try:
        await wait_for(lambda: len(model.requests) == 1)
        with pytest.raises(RuntimeError_, match="正在合并、放弃分支或压缩"):
            await rt.delete(session.id)
        with pytest.raises(RuntimeError_, match="正在合并、放弃分支或压缩"):
            rt.start(session.id, "不能并发改日志")
        assert session.path.exists()
    finally:
        compacting.cancel()
        await asyncio.gather(compacting, return_exceptions=True)
        await rt.delete(session.id)
        await rt.shutdown()


@pytest.mark.anyio
async def test_delete_discards_real_branch_and_worktree(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    rt = runtime(tmp_path)
    session = rt.create(str(repo), isolate=True)
    assert session.branch is not None
    branch = session.branch["branch"]
    worktree = Path(session.branch["worktree"])
    await rt.delete(session.id)
    assert not worktree.exists() and branch not in sh(repo, "branch")
    assert not session.path.exists() and rt.store.list() == []
    assert (repo / "calc.py").exists()


@pytest.mark.anyio
async def test_failed_branch_cleanup_keeps_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = make_repo(tmp_path / "repo")
    rt = runtime(tmp_path)
    session = rt.create(str(repo), isolate=True)

    def failed(*_args: object) -> None:
        raise BranchError("目录被占用")

    with monkeypatch.context() as scoped:
        scoped.setattr(runnermod, "discard_branch", failed)
        with pytest.raises(RuntimeError_, match="目录被占用"):
            await rt.delete(session.id)
    assert session.path.exists() and rt.store.get(session.id) is session
    await rt.delete(session.id)


@pytest.mark.anyio
async def test_management_api_rename_search_delete_and_running_conflict(tmp_path: Path) -> None:
    rt = runtime(tmp_path, Scripted([["还在想"]], delay=60))
    session = rt.create(str(tmp_path))
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    other = rt.create(str(other_dir))
    rt.rename(other.id, "同名 Needle")
    app.dependency_overrides[get_runtime] = lambda: rt
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            renamed = await client.patch(f"/agent/sessions/{session.id}", json={"title": "Needle 标题"})
            assert renamed.status_code == 200 and renamed.json()["title"] == "Needle 标题"
            found = await client.get("/agent/sessions", params={"q": "nEeDle", "cwd": str(tmp_path).upper().replace("\\", "/")})
            assert [s["id"] for s in found.json()] == [session.id]
            for title in ("", " ", "字" * 101):
                assert (await client.patch(f"/agent/sessions/{session.id}", json={"title": title})).status_code == 422
            rt.start(session.id, "做点事")
            assert (await client.delete(f"/agent/sessions/{session.id}")).status_code == 409
            rt.interrupt(session.id)
            await asyncio.gather(rt._tasks[session.id])
            assert (await client.delete(f"/agent/sessions/{session.id}")).json() == {"ok": True}
            assert (await client.get(f"/agent/sessions/{session.id}")).status_code == 404
            assert (await client.delete(f"/agent/sessions/{session.id}")).status_code == 404
            assert (await client.patch(f"/agent/sessions/{session.id}", json={"title": "不存在"})).status_code == 404
            assert (await client.patch(f"/agent/sessions/{other.id}", json={"title": "不能改"}, headers={"Origin": "https://evil.example"})).status_code == 403
            assert (await client.delete(f"/agent/sessions/{other.id}", headers={"Origin": "https://evil.example"})).status_code == 403
    finally:
        app.dependency_overrides.pop(get_runtime, None)
        await rt.shutdown()


@pytest.fixture(params=["embedded", "http"])
async def backend(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[Backend]:
    rt = runtime(tmp_path)
    if request.param == "embedded":
        backend = EmbeddedBackend(rt)
    else:
        app.dependency_overrides[get_runtime] = lambda: rt
        backend = HttpBackend("http://testserver", transport=httpx.ASGITransport(app=app))
    try:
        yield backend
    finally:
        app.dependency_overrides.pop(get_runtime, None)
        await backend.close()
        await rt.shutdown()


@pytest.mark.anyio
async def test_both_backends_rename_delete_and_errors(tmp_path: Path, backend: Backend) -> None:
    info = await backend.create(str(tmp_path), "ask")
    assert (await backend.rename(info["id"], "新名字"))["title"] == "新名字"
    assert (await backend.session(info["id"]))["title"] == "新名字"
    with pytest.raises(BackendError, match="1–100"):
        await backend.rename(info["id"], " ")
    with pytest.raises(BackendError):
        await backend.rename(info["id"], " " + "字" * 100 + " ")
    await backend.delete(info["id"])
    assert await backend.sessions() == []
    with pytest.raises(BackendError, match="没有这个会话"):
        await backend.delete(info["id"])


@pytest.mark.anyio
async def test_terminal_rename_filtered_resume_and_existing_id_number_usage(tmp_path: Path, backend: Backend) -> None:
    first = await backend.create(str(tmp_path), "ask")
    await backend.rename(first["id"], "Needle 购物车")
    second = await backend.create(str(tmp_path), "ask")
    await backend.rename(second["id"], "别的任务")
    console = recording()
    with create_pipe_input() as inp:
        term = Terminal(backend, console, second, inp=inp, output=DummyOutput())
        await term.command("/resume needle")
        listed = console.export_text(clear=True)
        assert "Needle 购物车" in listed and "别的任务" not in listed
        assert term.sid == second["id"]  # 关键词只筛选，等用户选编号
        await term.command("/resume 1")
        assert term.sid == first["id"]
        await term.command("/rename 换个名字")
        assert term.info["title"] == "换个名字"
        assert (await backend.session(term.sid))["title"] == "换个名字"
        await term.command(f"/resume {second['id']}")
        assert term.sid == second["id"]
        await term.command("/resume 不存在的关键词")
        await term.command("/resume 1")
        assert term.sid == second["id"] and "没有这个编号" in console.export_text()


@pytest.mark.anyio
async def test_terminal_delete_confirms_then_creates_same_project_session(tmp_path: Path, backend: Backend, monkeypatch: pytest.MonkeyPatch) -> None:
    info = await backend.create(str(tmp_path), "auto")
    with create_pipe_input() as inp:
        term = Terminal(backend, recording(), info, inp=inp, output=DummyOutput())
        prompt = AsyncMock(side_effect=["n", "y"])
        monkeypatch.setattr(term.prompt, "prompt_async", prompt)
        await term.command("/delete")
        assert term.sid == info["id"] and len(await backend.sessions()) == 1
        await term.command("/delete")
        assert prompt.await_count == 2
        assert term.sid != info["id"] and term.info["cwd"] == info["cwd"] and term.info["mode"] == "auto"
        assert [s["id"] for s in await backend.sessions()] == [term.sid]
        with pytest.raises(BackendError):
            await backend.session(info["id"])


@pytest.mark.anyio
async def test_terminal_keyword_search_can_find_older_than_default_page(tmp_path: Path, backend: Backend) -> None:
    older = await backend.create(str(tmp_path), "ask")
    await backend.rename(older["id"], "Needle 老会话")
    for _ in range(51):
        current = await backend.create(str(tmp_path), "ask")
    assert older["id"] not in {s["id"] for s in await backend.sessions()}
    with create_pipe_input() as inp:
        term = Terminal(backend, recording(), current, inp=inp, output=DummyOutput())
        await term.command("/resume needle")
        await term.command("/resume 1")
        assert term.sid == older["id"]
