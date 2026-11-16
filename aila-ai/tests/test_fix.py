"""修代码：用真的 Git 仓库（临时目录）验证——改动算得对、测试通过才改到用户的代码里、
没通过自动撤回、用户这期间改过文件就不覆盖、用户没提交的改动和新文件带进测试、依赖链接拆得干净"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.code import CodeProjects
from app.code import fix as fixmod
from app.code.fix import Edit, FixError, FixRunner, TestCommand, detect_tests, fix_stats, plan_edits
from app.tools.code import code_skill

CALC = "def add(a, b):\n    return a - b\n\n\ndef mul(a, b):\n    return a * b\n"
TEST = "from calc import add\nfrom extra import ONE\n\n\ndef test_add():\n    assert add(2, ONE) == 3\n"


def sh(repo: Path, *args: str) -> str:
    # 按 UTF-8 解码：不加 -X utf8 跑测试时（修复流程里就是这样跑的），text=True 会按系统编码把中文解成乱码
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True).stdout.decode("utf-8")


def make_repo(root: Path) -> Path:
    """一个小项目：calc.add 写错了；测试依赖一个还没提交的新文件 extra.py（检验基线带上了用户的新文件）"""
    root.mkdir(parents=True)
    (root / "calc.py").write_text(CALC, encoding="utf-8")
    (root / "notes.py").write_text("X = 1\n", encoding="utf-8")
    (root / "pyproject.toml").write_text('[tool.pytest.ini_options]\npythonpath = ["."]\n', encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_calc.py").write_text(TEST, encoding="utf-8")
    sh(root, "init", "-q", "-b", "main")
    sh(root, "-c", "core.autocrlf=false", "add", "-A")
    sh(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    (root / "extra.py").write_text("ONE = 1\n", encoding="utf-8")  # 没提交的新文件
    (root / "notes.py").write_text("X = 2  # 用户正在改，还没提交\n", encoding="utf-8")  # 没提交的改动
    return root


def pytest_cmd() -> list[TestCommand]:
    return [TestCommand("", [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"], "pytest", "python")]


FIX = Edit("calc.py", "    return a - b", "    return a + b")
WRONG = Edit("calc.py", "    return a - b", "    return a * b")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path / "proj")


# ---------- 算改动 ----------


def test_plan_computes_a_real_diff(repo: Path) -> None:
    plan = plan_edits(repo, [FIX])
    assert [c.file for c in plan.changes] == ["calc.py"]
    assert "-    return a - b\n+    return a + b" in plan.diff
    assert (repo / "calc.py").read_text(encoding="utf-8") == CALC  # 算 diff 不写文件


def test_plan_keeps_windows_line_endings(repo: Path) -> None:
    (repo / "calc.py").write_bytes(CALC.replace("\n", "\r\n").encode())
    plan = plan_edits(repo, [Edit("calc.py", "def add(a, b):\n    return a - b", "def add(a, b):\n    return a + b")])
    assert plan.changes[0].after == CALC.replace("a - b", "a + b").replace("\n", "\r\n").encode()


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (Edit("calc.py", "return a / b", "x"), "找不到要换掉的原文"),
        (Edit("calc.py", "    return a", "x"), "出现了 2 次"),
        (Edit("../outside.py", "", "x"), "不在项目里"),
        (Edit(".git/config", "x", "y"), "不在项目里"),
        (Edit("missing.py", "old", "x"), "不存在"),
        (Edit("calc.py", "", "x"), "要给出要换掉的原文"),
    ],
)
def test_plan_rejects_bad_edits(repo: Path, edit: Edit, message: str) -> None:
    with pytest.raises(FixError, match=message):
        plan_edits(repo, [edit])


def test_needs_a_git_repo(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(FixError, match="不是 Git 仓库"):
        plan_edits(tmp_path, [Edit("a.py", "x = 1", "x = 2")])


def test_detects_test_commands(tmp_path: Path) -> None:
    root = tmp_path / "mono"
    (root / "py" / "tests").mkdir(parents=True)
    (root / "py" / "pyproject.toml").write_text("", encoding="utf-8")
    exe = root / "py" / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    exe.parent.mkdir(parents=True)
    exe.write_text("", encoding="utf-8")
    (root / "web" / "src").mkdir(parents=True)
    (root / "web" / "package.json").write_text(json.dumps({"scripts": {"test": "vitest run"}}), encoding="utf-8")
    (root / "server").mkdir()
    (root / "server" / "pom.xml").write_text("<project/>", encoding="utf-8")

    cmds = {c.cwd: c for c in detect_tests(root, ["py/app/x.py", "web/src/a.ts", "server/src/main/java/A.java", "README.md"])}
    assert cmds["py"].argv[:3] == [str(exe), "-m", "pytest"] and cmds["py"].kind == "python"
    assert cmds["web"].kind == "node" and cmds["web"].argv[1] == "test"
    assert cmds["server"].kind == "maven" and cmds["server"].argv[-1] == "test"
    assert set(cmds) == {"py", "web", "server"}  # README 不属于任何子项目：不跑


# ---------- 在新分支上改、测、合 ----------


def test_passing_fix_is_applied_and_branch_kept(repo: Path, tmp_path: Path) -> None:
    runner = FixRunner(tmp_path / "fixes.jsonl", work_dir=tmp_path / "work")
    result = runner.run(plan_edits(repo, [FIX]), pytest_cmd())

    assert result.passed and result.merged, result.reason
    assert (repo / "calc.py").read_text(encoding="utf-8") == CALC.replace("a - b", "a + b")
    # 用户没提交的改动和新文件原样还在，没被提交、没被动过
    assert (repo / "notes.py").read_text(encoding="utf-8") == "X = 2  # 用户正在改，还没提交\n"
    assert (repo / "extra.py").exists()
    status = sh(repo, "status", "--porcelain")
    assert " M calc.py" in status and " M notes.py" in status and "?? extra.py" in status
    # 修复记录在分支上，临时目录清掉了
    assert result.branch in sh(repo, "branch", "--list", "aila/*")
    assert sh(repo, "log", "-1", "--format=%an", result.branch).strip() == "艾拉"
    assert len(sh(repo, "worktree", "list").strip().splitlines()) == 1
    assert fix_stats(tmp_path / "fixes.jsonl") == {"attempts": 1, "tested": 1, "passed": 1, "merged": 1}


def test_failing_fix_is_rolled_back(repo: Path, tmp_path: Path) -> None:
    runner = FixRunner(tmp_path / "fixes.jsonl", work_dir=tmp_path / "work")
    result = runner.run(plan_edits(repo, [WRONG]), pytest_cmd())

    assert not result.passed and not result.merged
    assert "测试没通过" in result.reason and "你的代码没动" in result.reason
    assert "assert" in result.tests[0].tail  # 失败的输出交回大模型
    assert (repo / "calc.py").read_text(encoding="utf-8") == CALC
    assert sh(repo, "branch", "--list", "aila/*").strip() == ""  # 分支也删了
    assert len(sh(repo, "worktree", "list").strip().splitlines()) == 1


def test_does_not_overwrite_user_edits_made_during_tests(repo: Path, tmp_path: Path) -> None:
    """测试跑的这段时间里用户又改了同一个文件：测试过了也不写，免得覆盖"""
    user_edit = f"open(r'{repo / 'calc.py'}', 'a', encoding='utf-8').write('# 用户刚加的\\n')"
    cmds = [TestCommand("", [sys.executable, "-c", user_edit], "模拟用户改文件", "python")]
    result = FixRunner(tmp_path / "fixes.jsonl", work_dir=tmp_path / "work").run(plan_edits(repo, [FIX]), cmds)

    assert result.passed and not result.merged
    assert "为了不覆盖你的改动" in result.reason
    assert (repo / "calc.py").read_text(encoding="utf-8").endswith("# 用户刚加的\n")
    assert result.branch in sh(repo, "branch", "--list", "aila/*")  # 改动留在分支上


def test_without_tests_nothing_is_applied(repo: Path, tmp_path: Path) -> None:
    result = FixRunner(tmp_path / "fixes.jsonl", work_dir=tmp_path / "work").run(plan_edits(repo, [FIX]), [])
    assert not result.merged and "没找到测试命令" in result.reason
    assert (repo / "calc.py").read_text(encoding="utf-8") == CALC
    assert f"git diff {result.branch}~1 {result.branch} | git apply" in result.reason


def test_node_modules_link_is_removed_without_touching_the_original(repo: Path, tmp_path: Path) -> None:
    (repo / "package.json").write_text(json.dumps({"scripts": {"test": "x"}}), encoding="utf-8")
    deps = repo / "node_modules" / "left-pad"
    deps.mkdir(parents=True)
    (deps / "index.js").write_text("module.exports = 1\n", encoding="utf-8")
    (repo / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    check = "import os, sys; sys.exit(0 if os.path.isfile('node_modules/left-pad/index.js') else 1)"
    cmds = [TestCommand("", [sys.executable, "-c", check], "依赖在不在", "node")]
    result = FixRunner(tmp_path / "fixes.jsonl", work_dir=tmp_path / "work").run(plan_edits(repo, [FIX]), cmds)

    assert result.passed, result.reason  # 临时目录里能用到原来的依赖
    assert (deps / "index.js").exists()  # 删临时目录没有顺着链接删掉原来的依赖


# ---------- 工具 ----------


@pytest.mark.anyio
async def test_code_fix_tool(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fixmod, "_python_for", lambda *_: sys.executable)
    projects = CodeProjects(tmp_path / "data", lambda: None)
    projects.fixer = FixRunner(tmp_path / "fixes.jsonl", work_dir=tmp_path / "work")
    projects.add(str(repo))
    tool = next(t for t in code_skill(projects).tools if t.name == "code_fix")
    args = {"summary": "add 写成了减法", "edits": [{"file": "calc.py", "old": "    return a - b", "new": "    return a + b"}]}

    assert tool.risk == "write"  # 每次都要用户点头
    assert tool.validate is not None and tool.detail is not None
    assert tool.validate({**args, "edits": [{"file": "calc.py", "old": "nope", "new": "x"}]}).startswith("calc.py 里找不到")
    detail = tool.detail(args)
    assert "项目：proj" in detail and "add 写成了减法" in detail
    assert "跑测试：pytest（proj）" in detail and "+    return a + b" in detail

    report = await tool.run(args)
    assert "测试通过，已经改到你的代码里" in report
    assert (repo / "calc.py").read_text(encoding="utf-8") == CALC.replace("a - b", "a + b")
