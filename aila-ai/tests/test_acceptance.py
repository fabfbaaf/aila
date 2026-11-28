"""验收和交付：交付卡（改动、测试、PR、CI 都由代码算）、验收记录、退回修改接着做"""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.deps import get_runtime
from app.main import app
from app.runtime import acceptance
from app.runtime.runner import Runtime

from .test_runtime import SHELL, Scripted, call


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "cart.py").write_text("def total(items):\n    return sum(items)\n", encoding="utf-8")
    (root / "README.md").write_text("# 小商店\n", encoding="utf-8")
    git(root, "init", "-q")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "add", ".")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    return root


def edit(file: str, seq: int = 1) -> dict[str, Any]:
    return {"type": "tool", "id": f"e{seq}", "name": "edit_file", "status": "ok", "file": file, "content": "改好了", "seq": seq}


def run(cid: str, command: str, status: str = "ok", content: str = "") -> list[dict[str, Any]]:
    return [{"type": "assistant", "text": "", "tool_calls": [{"id": cid, "name": "run_command", "arguments": json.dumps({"command": command})}]},
            {"type": "tool", "id": cid, "name": "run_command", "status": status, "content": content}]


def test_verdict_counts_only_until_the_next_turn() -> None:
    events: list[dict[str, Any]] = [{"type": "turn_end"}]
    assert acceptance.verdict_of(events) is None
    events.append({"type": "acceptance", "verdict": "accepted"})
    assert acceptance.verdict_of(events) == "accepted"
    events.append({"type": "turn_end"})  # 验收后又做了一轮：要重新看
    assert acceptance.verdict_of(events) is None
    events.append({"type": "acceptance", "verdict": "rejected"})
    assert acceptance.verdict_of(events) == "rejected"
    assert acceptance.latest(events)["verdict"] == "rejected"


def test_comments_are_cleaned_and_rejection_text_is_built_by_code() -> None:
    raw = [{"file": "src/cart.py", "line": 2, "text": "空列表要返回 0", "extra": "x"},
           {"file": "a.py", "line": 0, "text": "行号不对"}, {"file": "", "line": 3, "text": "没文件"},
           {"file": "b.py", "line": 4, "side": "old", "text": "这行别删"}, "乱写的", {"file": "c.py", "line": "5", "text": "x"}]
    clean = acceptance.clean_comments(raw)
    assert clean == [{"file": "src/cart.py", "line": 2, "side": "new", "text": "空列表要返回 0"},
                     {"file": "b.py", "line": 4, "side": "old", "text": "这行别删"}]
    assert acceptance.clean_comments("不是列表") == []
    text = acceptance.rejection_text("  测试没覆盖到  ", clean)
    assert text.startswith("这次的改动验收没通过")
    assert "总的意见：测试没覆盖到" in text
    assert "- src/cart.py 第 2 行：空列表要返回 0" in text
    assert "- b.py 第 4 行（删掉的那行）：这行别删" in text
    assert "总的意见" not in acceptance.rejection_text("", clean)


def test_last_tests_and_pr_link_come_from_the_log() -> None:
    events = [*run("a", "npm.cmd run typecheck"), *run("b", ".venv\\Scripts\\python -m pytest -q", "error", "1 failed"),
              *run("c", "npx.cmd vitest run", "ok", "Tests 12 passed"), *run("d", "git status")]
    tests = acceptance.last_tests(events)
    assert tests is not None and tests["command"] == "npx.cmd vitest run" and tests["ok"] and "12 passed" in tests["tail"]
    assert acceptance.last_tests(run("x", "git status")) is None
    failed = acceptance.last_tests(run("b", "pytest", "error", "2 failed"))
    assert failed is not None and not failed["ok"]
    log = [{"type": "tool", "content": "https://github.com/a/b/pull/3"}, {"type": "assistant", "text": "开好了：https://github.com/o/r-x/pull/12"}]
    assert acceptance.pr_of(log) == {"url": "https://github.com/o/r-x/pull/12", "owner": "o", "repo": "r-x", "number": 12}
    assert acceptance.pr_of([]) is None


def test_ci_from_public_api_when_gh_is_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(acceptance.shutil, "which", lambda _name: None)
    pr = {"url": "https://github.com/o/r/pull/1", "owner": "o", "repo": "r", "number": 1}
    asked = []

    def fetch(url: str) -> Any:
        asked.append(url)
        if url.endswith("/pulls/1"):
            return {"head": {"sha": "abc"}, "state": "open", "merged": False}
        return {"check_runs": [{"name": "test", "status": "completed", "conclusion": "success"},
                               {"name": "lint", "status": "in_progress", "conclusion": None}]}

    ci = acceptance.ci_status(pr, tmp_path, fetch)
    assert asked == ["https://api.github.com/repos/o/r/pulls/1", "https://api.github.com/repos/o/r/commits/abc/check-runs"]
    assert ci is not None and ci["state"] == "pending" and ci["open"] and [c["state"] for c in ci["checks"]] == ["pass", "pending"]

    def broken(url: str) -> Any:
        raise OSError("断网")

    down = acceptance.ci_status(pr, tmp_path, broken)
    assert down is not None and down["state"] == "unknown" and "gh" in down["note"]
    assert acceptance.ci_status(None, tmp_path, fetch) is None


def test_changes_in_project_only_count_files_this_task_edited(repo: Path) -> None:
    (repo / "src" / "cart.py").write_text("def total(items):\n    return sum(items or [])\n", encoding="utf-8")
    (repo / "README.md").write_text("# 小商店（你自己改的）\n", encoding="utf-8")  # 不是这个任务改的
    (repo / "src" / "new.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    out = acceptance.changes(repo, [edit("src/cart.py", 1), edit("src/new.py", 2)], None)
    by = {f["path"]: f for f in out["files"]}
    assert set(by) == {"src/cart.py", "src/new.py"}
    assert by["src/cart.py"]["added"] == 1 and by["src/cart.py"]["deleted"] == 1
    assert "+    return sum(items or [])" in by["src/cart.py"]["diff"]
    assert by["src/new.py"]["untracked"] and by["src/new.py"]["added"] == 2 and "@@ -0,0 +1,2 @@" in by["src/new.py"]["diff"]
    assert acceptance.changes(repo, [], None)["note"] == "这个任务还没改过文件。"
    gone = acceptance.changes(repo, [edit("src/cart.py")], {"base": "x"}, branch_gone=True)
    assert [f["path"] for f in gone["files"]] == ["src/cart.py"] and "合并或放弃" in gone["note"]


def test_changes_on_a_branch_compare_with_where_it_started(repo: Path) -> None:
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    (repo / "README.md").write_text("# 小商店\n\n多一行\n", encoding="utf-8")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "她提交的")
    (repo / "src" / "cart.py").write_text("def total(items):\n    return 0\n", encoding="utf-8")
    out = acceptance.changes(repo, [], {"base": base, "branch": "aila/x"})
    assert sorted(f["path"] for f in out["files"]) == ["README.md", "src/cart.py"]  # 提交了的、没提交的都算
    # 在子目录里做：路径相对子目录，也只看子目录里的
    sub = acceptance.changes(repo / "src", [], {"base": base, "branch": "aila/x"})
    assert [f["path"] for f in sub["files"]] == ["cart.py"]


def test_changes_outside_git(tmp_path: Path) -> None:
    out = acceptance.changes(tmp_path, [edit("a.txt")], None)
    assert [f["path"] for f in out["files"]] == ["a.txt"] and "不是 git 仓库" in out["note"]


@pytest.mark.anyio
async def test_reject_with_line_comments_starts_a_new_turn(repo: Path, tmp_path: Path) -> None:
    model = Scripted([[call("write_file", path="src/new.py", content="x = 1\n"), call("run_command", command="echo pytest ok")],
                      ["改好了，测试也过了。"], ["按意见改好了。"]])
    rt = Runtime(tmp_path / "data", lambda: model, shell=SHELL)
    sid = rt.create(str(repo), mode="auto").id
    with pytest.raises(Exception, match="还没做完一轮"):
        rt.accept(sid, "accepted")
    async for _ in rt.run(sid, "加个文件"):
        pass
    card = await rt.delivery(sid, with_ci=False)
    assert [f["path"] for f in card["changes"]["files"]] == ["src/new.py"]
    assert card["tests"]["command"] == "echo pytest ok" and card["tests"]["ok"]
    assert card["pr"] is None and card["ci"] is None and card["verdict"] is None

    with pytest.raises(Exception, match="要写一句意见"):
        rt.accept(sid, "rejected")
    event = rt.accept(sid, "rejected", "", [{"file": "src/new.py", "line": 1, "text": "x 改成 2"}])
    assert event["type"] == "acceptance" and event["verdict"] == "rejected"
    async for e in rt.subscribe(sid, event["seq"]):
        if e["type"] == "turn_end":
            break
    session = rt.agent(sid).session
    asked = [e for e in session.events if e["type"] == "user"][-1]
    assert asked.get("rejection") and asked["title"] == "退回修改（1 条意见）"
    assert "- src/new.py 第 1 行：x 改成 2" in asked["text"]
    assert session.summary()["acceptance"] is None  # 退回以后又做了一轮：等你再看

    rt.accept(sid, "accepted", "可以了")
    assert session.summary()["acceptance"] == "accepted"
    assert len([e for e in session.events if e["type"] == "user"]) == 2  # 通过不会再开一轮


def test_api_delivery_and_acceptance(repo: Path, tmp_path: Path) -> None:
    rt = Runtime(tmp_path / "data", lambda: Scripted([["看过了。"]]), shell=SHELL)
    app.dependency_overrides[get_runtime] = lambda: rt
    try:
        client = TestClient(app)
        sid = client.post("/agent/sessions", json={"cwd": str(repo)}).json()["id"]
        assert client.get("/agent/sessions/20990101-000000-abcdef/delivery").status_code == 404
        assert client.post("/agent/sessions/20990101-000000-abcdef/acceptance", json={"verdict": "accepted"}).status_code == 404
        assert client.post(f"/agent/sessions/{sid}/acceptance", json={"verdict": "accepted"}).status_code == 409
        assert client.post(f"/agent/sessions/{sid}/acceptance", json={"verdict": "maybe"}).status_code == 422
        client.post(f"/agent/sessions/{sid}/messages", json={"text": "看看"})
        card = client.get(f"/agent/sessions/{sid}/delivery?ci=false").json()
        assert card["changes"]["files"] == [] and card["tests"] is None
        done = client.post(f"/agent/sessions/{sid}/acceptance", json={"verdict": "accepted", "note": "好"}).json()
        assert done["verdict"] == "accepted" and done["note"] == "好"
        assert client.get(f"/agent/sessions/{sid}").json()["acceptance"] == "accepted"
        assert client.get(f"/agent/sessions/{sid}/delivery?ci=false").json()["acceptance"]["note"] == "好"
    finally:
        app.dependency_overrides.pop(get_runtime, None)
