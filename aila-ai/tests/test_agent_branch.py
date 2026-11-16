"""「在新分支上做」：用真的 git 仓库验证——她在临时目录里改，你的代码不动；合并前跑测试、查冲突；放弃时拆干净"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.deps import get_runtime
from app.llm import ToolCall
from app.main import app
from app.runtime import branch as branchmod
from app.runtime.runner import Runtime, RuntimeError_

from .test_fix import make_repo, pytest_cmd, sh
from .test_runtime import SHELL, Scripted, drive


def edit(old: str, new: str, path: str = "calc.py") -> list[ToolCall]:
    return [
        ToolCall("r1", "read_file", json.dumps({"path": path})),
        ToolCall("e1", "edit_file", json.dumps({"path": path, "old_string": old, "new_string": new})),
    ]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path / "proj")


@pytest.fixture(autouse=True)
def tests_with_this_python(monkeypatch: pytest.MonkeyPatch) -> None:
    """测试命令用跑测试的这个 python（临时仓库里没有虚拟环境）"""
    monkeypatch.setattr(branchmod, "detect_tests", lambda repo, files: pytest_cmd())


def runtime(tmp_path: Path, steps: list[list]) -> Runtime:  # type: ignore[type-arg]
    return Runtime(tmp_path / "data", lambda: Scripted(steps), shell=SHELL)


@pytest.mark.anyio
async def test_work_on_a_branch_then_merge(repo: Path, tmp_path: Path) -> None:
    rt = runtime(tmp_path, [edit("    return a - b", "    return a + b"), ["改好了。"]])
    session = rt.create(str(repo), "auto", isolate=True)
    wt = session.workdir
    assert wt != repo and (wt / "extra.py").exists()  # 你没提交的新文件也带过去了
    assert "X = 2" in (wt / "notes.py").read_text(encoding="utf-8")
    assert "<分支>" in session.context and session.summary()["branch"].startswith("aila/task-")

    await drive(rt.agent(session.id), "修好 add")
    assert "a - b" in (repo / "calc.py").read_text(encoding="utf-8")  # 你的代码一个字没动
    assert "a + b" in (wt / "calc.py").read_text(encoding="utf-8")

    event = await rt.merge(session.id)
    assert event["merged"] and event["files"] == ["calc.py"] and event["tests"][0]["ok"]
    assert "a + b" in (repo / "calc.py").read_text(encoding="utf-8")
    assert "X = 2" in (repo / "notes.py").read_text(encoding="utf-8")  # 你自己的改动还在
    assert sh(repo, "status", "--porcelain").count("calc.py") == 1  # 合并了但没有替你提交

    again = await rt.merge(session.id)  # 合并过的不再合并
    assert not again["merged"] and "还没有新的改动" in again["reason"]


@pytest.mark.anyio
async def test_failing_tests_block_the_merge(repo: Path, tmp_path: Path) -> None:
    rt = runtime(tmp_path, [edit("    return a - b", "    return a * b"), ["改了。"]])
    session = rt.create(str(repo), "auto", isolate=True)
    await drive(rt.agent(session.id), "改 add")
    event = await rt.merge(session.id)
    assert not event["merged"] and "测试没通过" in event["reason"] and not event["tests"][0]["ok"]
    assert "a - b" in (repo / "calc.py").read_text(encoding="utf-8")
    assert session.branch is not None  # 分支还在，可以让她接着改


@pytest.mark.anyio
async def test_your_own_edits_are_not_overwritten(repo: Path, tmp_path: Path) -> None:
    rt = runtime(tmp_path, [edit("    return a - b", "    return a + b"), ["改好了。"]])
    session = rt.create(str(repo), "auto", isolate=True)
    await drive(rt.agent(session.id), "修好 add")
    (repo / "calc.py").write_text("# 你自己也在改\n" + (repo / "calc.py").read_text(encoding="utf-8"), encoding="utf-8")
    event = await rt.merge(session.id)
    assert not event["merged"] and event["conflicts"] == ["calc.py"]
    assert (repo / "calc.py").read_text(encoding="utf-8").startswith("# 你自己也在改")


@pytest.mark.anyio
async def test_merge_without_tests_when_asked(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(branchmod, "detect_tests", lambda repo, files: [])
    rt = runtime(tmp_path, [edit("X = 2  # 用户正在改，还没提交", "X = 3", "notes.py"), ["改了。"]])
    session = rt.create(str(repo), "auto", isolate=True)
    await drive(rt.agent(session.id), "改 notes")
    first = await rt.merge(session.id)
    assert not first["merged"] and "没找到这个项目的测试命令" in first["reason"]
    forced = await rt.merge(session.id, run_tests=False)
    assert forced["merged"] and (repo / "notes.py").read_text(encoding="utf-8") == "X = 3\n"


@pytest.mark.anyio
async def test_discard_cleans_up_and_keeps_dependencies(repo: Path, tmp_path: Path) -> None:
    (repo / ".gitignore").write_text(".venv/\n", encoding="utf-8")
    (repo / ".venv").mkdir()
    (repo / ".venv" / "keep.txt").write_text("依赖", encoding="utf-8")
    rt = runtime(tmp_path, [["好"]])
    session = rt.create(str(repo), "ask", isolate=True)
    wt = Path(session.branch["worktree"])  # type: ignore[index]
    assert (wt / ".venv" / "keep.txt").exists()  # 依赖链过去了，不用重新装
    name = session.branch["branch"]  # type: ignore[index]

    await rt.discard(session.id)
    assert not wt.exists() and (repo / ".venv" / "keep.txt").exists()  # 原来的依赖没被顺着链接删掉
    assert name not in sh(repo, "branch")
    assert session.branch is None
    with pytest.raises(RuntimeError_, match="分支已经放弃了"):
        rt.start(session.id, "再做点事")


def test_needs_a_git_repo_with_commits(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    rt = runtime(tmp_path, [["好"]])
    with pytest.raises(RuntimeError_, match="不在 git 仓库里"):
        rt.create(str(plain), isolate=True)
    empty = tmp_path / "empty"
    empty.mkdir()
    sh(empty, "init", "-q")
    with pytest.raises(RuntimeError_, match="还没有任何提交"):
        rt.create(str(empty), isolate=True)


def test_branch_api(repo: Path, tmp_path: Path) -> None:
    rt = runtime(tmp_path, [["好"]])
    app.dependency_overrides[get_runtime] = lambda: rt
    try:
        client = TestClient(app)
        created = client.post("/agent/sessions", json={"cwd": str(repo), "isolate": True}).json()
        assert created["branch"].startswith("aila/task-")
        sid = created["id"]
        merged = client.post(f"/agent/sessions/{sid}/merge", json={}).json()
        assert merged["type"] == "merge" and not merged["merged"]
        closed = client.post(f"/agent/sessions/{sid}/discard").json()
        assert closed["type"] == "branch_closed"
        assert client.post(f"/agent/sessions/{sid}/discard").status_code == 409
        plain = client.post("/agent/sessions", json={"cwd": str(repo)}).json()
        assert plain["branch"] is None
        assert client.post(f"/agent/sessions/{plain['id']}/merge", json={}).status_code == 409
    finally:
        app.dependency_overrides.pop(get_runtime, None)
