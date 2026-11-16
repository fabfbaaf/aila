"""Agent 修代码：在新分支上改、跑项目自带的测试，通过才改到用户的代码里，失败自动撤回。

用户有两次把关：
1. 看 diff 点头（diff 由代码按真实文件算出来，不用大模型的描述）
2. 测试自动把关：项目自带的测试全过，才把改动写进用户的文件

流程：
- 大模型给出「哪个文件、哪段原文、换成什么」（原文必须在文件里唯一出现，不给行号——行号最容易错）
- 用 git worktree 在临时目录开一个新分支 aila/fix-xxxx：先放进用户现在没提交的改动和新文件（基线），
  再放进修复，各提交一次。用户的工作区这时一个字都没动
- 在那个目录里跑测试。测试命令按项目自动识别（pytest / npm test / mvnw test），不由大模型决定：
  大模型就算被网页、代码注释里的话带偏，也没法让这里执行任意命令
- 全过：确认用户在这期间没改过这几个文件，再把修复写进去；分支留着备查
- 没过：删掉分支和临时目录，用户的代码保持原样，把失败的输出交回大模型
"""

import difflib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

MAX_EDITS = 20
MAX_EDIT_CHARS = 30_000
MAX_UNTRACKED_FILES = 300
MAX_UNTRACKED_BYTES = 20_000_000
TEST_TIMEOUT = 600
OUTPUT_TAIL = 2500
DIFF_CHARS = 5000
GIT_TIMEOUT = 60

WIN = sys.platform == "win32"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if WIN else 0
# 修复分支上的两次提交是艾拉自己记账用的：不用用户的身份，也不触发用户的提交钩子（钩子可能改文件、跑很久）
AILA_GIT = ["-c", "user.name=艾拉", "-c", "user.email=aila@localhost", "-c", "commit.gpgsign=false"]


class FixError(Exception):
    """改不了：消息原样交给大模型（比如原文对不上），再由她转告用户"""


@dataclass(frozen=True)
class Edit:
    file: str  # 相对项目根目录
    old: str  # 要换掉的原文（在文件里唯一出现）；新建文件时为空
    new: str


@dataclass(frozen=True)
class FileChange:
    file: str  # 相对仓库根目录（git 的顶层目录）
    before: bytes | None  # None：新文件
    after: bytes


@dataclass(frozen=True)
class Plan:
    repo: Path  # git 仓库的顶层目录
    changes: list[FileChange]
    diff: str


@dataclass(frozen=True)
class TestCommand:
    __test__ = False  # 名字以 Test 开头，告诉 pytest 这不是测试类

    cwd: str  # 相对仓库根目录（/ 分隔，"" 是根目录）
    argv: list[str]
    label: str  # 给人看的：pytest（aila-ai）
    kind: str  # python / node / maven / gradle


@dataclass
class TestRun:
    __test__ = False

    label: str
    ok: bool
    seconds: float
    tail: str


@dataclass
class FixResult:
    id: str
    branch: str
    merged: bool
    passed: bool
    reason: str
    tests: list[TestRun] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    seconds: float = 0.0


# ---------- 算改动 ----------


def git(repo: Path, *args: str, stdin: bytes | None = None, check: bool = True) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), *args], input=stdin, capture_output=True, timeout=GIT_TIMEOUT, creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as err:
        raise FixError(f"git 运行失败：{err}") from err
    if check and out.returncode != 0:
        raise FixError(f"git {args[0]} 失败：{out.stderr.decode('utf-8', 'replace').strip()[:300]}")
    return out.stdout.decode("utf-8", "replace")


def repo_root(root: Path) -> Path:
    top = git(root, "rev-parse", "--show-toplevel", check=False).strip()
    if not top:
        raise FixError("这个项目不是 Git 仓库：修代码要在新分支上改，没有 Git 没法保证能撤回。")
    return Path(top).resolve()


def plan_edits(project_root: Path, edits: list[Edit]) -> Plan:
    """检查每处改动能不能对上现在的文件，算出改完的内容和 diff。一个字都不写"""
    if not edits:
        raise FixError("没有要改的地方。")
    if len(edits) > MAX_EDITS or sum(len(e.old) + len(e.new) for e in edits) > MAX_EDIT_CHARS:
        raise FixError("一次改动太多了，拆成小一点的修复。")
    root = project_root.resolve()
    repo = repo_root(root)

    by_file: dict[str, list[Edit]] = {}
    for e in edits:
        by_file.setdefault(e.file.replace("\\", "/").strip("/"), []).append(e)

    changes: list[FileChange] = []
    diff_parts: list[str] = []
    for rel, file_edits in by_file.items():
        path = (root / rel).resolve()
        if not path.is_relative_to(root) or ".git" in path.relative_to(root).parts:
            raise FixError(f"{rel} 不在项目里，不能改。")
        repo_rel = path.relative_to(repo).as_posix()

        if not path.exists():
            if len(file_edits) != 1 or file_edits[0].old:
                raise FixError(f"{rel} 不存在。要新建文件的话，原文留空、只给一处。")
            text_after = file_edits[0].new
            changes.append(FileChange(repo_rel, None, text_after.encode("utf-8")))
            diff_parts.append(_diff(repo_rel, "", text_after))
            continue

        raw = path.read_bytes()
        bom = raw.startswith(b"\xef\xbb\xbf")
        try:
            text = raw.decode("utf-8-sig" if bom else "utf-8")
        except UnicodeDecodeError as err:
            raise FixError(f"{rel} 不是 UTF-8 文本，不能改。") from err
        crlf = "\r\n" in text
        before_text = text
        for e in file_edits:
            if not e.old:
                raise FixError(f"改 {rel} 要给出要换掉的原文。")
            old, new = _eol(e.old, crlf), _eol(e.new, crlf)
            count = text.count(old)
            if count == 0:
                first = e.old.strip().splitlines()[0][:60] if e.old.strip() else ""
                raise FixError(f"{rel} 里找不到要换掉的原文（「{first}」…）。先用 code_read 看一下现在的代码，原文要一字不差。")
            if count > 1:
                raise FixError(f"{rel} 里要换掉的原文出现了 {count} 次，多带几行上下文，让它只出现一次。")
            text = text.replace(old, new, 1)
        if text == before_text:
            continue
        after = (b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8")
        changes.append(FileChange(repo_rel, raw, after))
        diff_parts.append(_diff(repo_rel, before_text, text))

    if not changes:
        raise FixError("改完和原来一样，没有要改的。")
    diff = "".join(diff_parts)
    if len(diff) > DIFF_CHARS:
        diff = diff[:DIFF_CHARS] + "\n…（diff 太长，后面省略）"
    return Plan(repo, changes, diff)


def _eol(text: str, crlf: bool) -> str:
    text = text.replace("\r\n", "\n")
    return text.replace("\n", "\r\n") if crlf else text


def _diff(rel: str, before: str, after: str) -> str:
    a = before.replace("\r\n", "\n").splitlines(keepends=True)
    b = after.replace("\r\n", "\n").splitlines(keepends=True)
    lines = difflib.unified_diff(a, b, fromfile=f"a/{rel}" if before else "/dev/null", tofile=f"b/{rel}", n=2)
    return "".join(line if line.endswith("\n") else line + "\n" for line in lines)


# ---------- 找测试命令 ----------


def detect_tests(repo: Path, files: list[str]) -> list[TestCommand]:
    """改到的文件属于哪个子项目，就跑那个子项目的测试（从文件往上找 pom.xml / package.json / pyproject.toml）"""
    found: dict[str, TestCommand] = {}
    for rel in files:
        d = (repo / rel).parent
        while d.is_relative_to(repo):
            cmd = _test_command(repo, d)
            if cmd:
                found.setdefault(cmd.cwd, cmd)
                break
            if d == repo:
                break
            d = d.parent
    return list(found.values())


def _test_command(repo: Path, d: Path) -> TestCommand | None:
    cwd = d.relative_to(repo).as_posix()
    cwd = "" if cwd == "." else cwd
    name = cwd or repo.name
    if (d / "pom.xml").exists():
        wrapper = d / ("mvnw.cmd" if WIN else "mvnw")
        argv = [str(wrapper)] if wrapper.exists() else ["mvn.cmd" if WIN else "mvn"]
        return TestCommand(cwd, [*argv, "-q", "-B", "test"], f"mvn test（{name}）", "maven")
    if (d / "build.gradle").exists() or (d / "build.gradle.kts").exists():
        wrapper = d / ("gradlew.bat" if WIN else "gradlew")
        if wrapper.exists():
            return TestCommand(cwd, [str(wrapper), "test", "-q"], f"gradle test（{name}）", "gradle")
    pkg = d / "package.json"
    if pkg.exists():
        try:
            script = json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {}).get("test", "")
        except ValueError:
            script = ""
        if script and "no test specified" not in script:
            return TestCommand(cwd, ["npm.cmd" if WIN else "npm", "test", "--silent"], f"npm test（{name}）", "node")
    if any((d / m).exists() for m in ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini")) and (d / "tests").is_dir():
        python = _python_for(repo, d)
        if python:
            return TestCommand(cwd, [python, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"], f"pytest（{name}）", "python")
    return None


def _python_for(repo: Path, d: Path) -> str | None:
    """用项目自己的虚拟环境（依赖装在那里）；没有就用系统的 python"""
    for base in (d, repo):
        for venv in (".venv", "venv", "env"):
            exe = base / venv / ("Scripts/python.exe" if WIN else "bin/python")
            if exe.exists():
                return str(exe)
    for name in ("python", "python3"):
        found = shutil.which(name)
        if found and "WindowsApps" not in found:  # Windows 自带的那个只会打开应用商店
            return found
    return None


# ---------- 在新分支上改、测、合 ----------


class FixRunner:
    def __init__(self, log_path: Path, work_dir: Path | None = None, test_timeout: float = TEST_TIMEOUT) -> None:
        self._log = log_path
        self._work = work_dir or Path(tempfile.gettempdir()) / "aila-fix"
        self._timeout = test_timeout
        self._lock = threading.Lock()  # 同一时间只修一处

    def run(self, plan: Plan, tests: list[TestCommand]) -> FixResult:
        with self._lock:
            return self._run(plan, tests)

    def _run(self, plan: Plan, tests: list[TestCommand]) -> FixResult:
        t0 = time.perf_counter()
        fid = secrets.token_hex(3)
        branch = f"aila/fix-{fid}"
        wt = self._work / f"aila-fix-{fid}"
        repo = plan.repo
        files = [c.file for c in plan.changes]
        result = FixResult(fid, branch, merged=False, passed=False, reason="", files=files)
        links: list[Path] = []
        created = False
        try:
            if not git(repo, "rev-parse", "--verify", "-q", "HEAD", check=False).strip():
                raise FixError("这个仓库还没有任何提交，没法开新分支。")
            self._work.mkdir(parents=True, exist_ok=True)
            git(repo, "worktree", "add", "-q", "-b", branch, str(wt), "HEAD")
            created = True
            self._copy_baseline(repo, wt)
            git(wt, *AILA_GIT, "add", "-A")
            git(wt, *AILA_GIT, "commit", "-q", "--no-verify", "--allow-empty", "-m", "艾拉：基线（你当时还没提交的改动）")
            for c in plan.changes:
                target = wt / c.file
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(c.after)
            git(wt, *AILA_GIT, "add", "-A")
            git(wt, *AILA_GIT, "commit", "-q", "--no-verify", "-m", f"艾拉：修复（{', '.join(files)[:150]}）")

            if not tests:
                result.reason = (f"这个项目没找到测试命令，没法自动验证，所以没改你的代码。改动留在分支 {branch} 上，"
                                 f"要用的话在仓库里运行：git diff {branch}~1 {branch} | git apply")
                return result

            for cmd in tests:
                links += self._link_deps(repo, wt, cmd)
                run = self._run_test(wt, cmd)
                result.tests.append(run)
                if not run.ok:
                    result.reason = f"测试没通过（{run.label}），改动已经撤回，你的代码没动。"
                    return result
            result.passed = True

            # 测试全过：确认这期间用户没改过这几个文件，再写进去
            for c in plan.changes:
                now = (repo / c.file).read_bytes() if (repo / c.file).exists() else None
                if now != c.before:
                    result.reason = (f"测试通过了，但你在这期间改过 {c.file}，为了不覆盖你的改动，没有写进去。"
                                     f"改动在分支 {branch} 上：git diff {branch}~1 {branch} | git apply")
                    return result
            for c in plan.changes:
                _atomic_write(repo / c.file, c.after)
            result.merged = True
            result.reason = f"测试通过，已经改到你的代码里。修复记录在分支 {branch} 上，可以随时查看。"
            return result
        except FixError as err:
            result.reason = str(err)
            return result
        finally:
            for link in links:  # 先拆掉链接，删临时目录时绝不能顺着链接删到原来的依赖
                _remove_link(link)
            if created:
                git(repo, "worktree", "remove", "--force", str(wt), check=False)
                shutil.rmtree(wt, ignore_errors=True)
                git(repo, "worktree", "prune", check=False)
                keep = result.merged or (result.passed and not result.merged) or not tests
                if not keep:
                    git(repo, "branch", "-D", branch, check=False)
            result.seconds = round(time.perf_counter() - t0, 1)
            self._record(repo, result)

    def _copy_baseline(self, repo: Path, wt: Path) -> None:
        copy_baseline(repo, wt)

    def _link_deps(self, repo: Path, wt: Path, cmd: TestCommand) -> list[Path]:
        """Node 项目的依赖（node_modules）不在 Git 里：链接到原来的，不用重新安装"""
        if cmd.kind != "node":
            return []
        dst = wt / cmd.cwd / "node_modules"
        return [dst] if link_dir(repo / cmd.cwd / "node_modules", dst) else []

    def _run_test(self, wt: Path, cmd: TestCommand) -> TestRun:
        return run_test(wt, cmd, self._timeout)

    def _record(self, repo: Path, result: FixResult) -> None:
        """每次修复都记一笔：自动修复的测试通过率、合并率就从这里算"""
        try:
            self._log.parent.mkdir(parents=True, exist_ok=True)
            entry = {"at": time.time(), "repo": str(repo), **asdict(result)}
            for t in entry["tests"]:
                t["tail"] = t["tail"][-300:]
            with self._log.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass


# ---------- 修代码和「在新分支上做」共用 ----------


def copy_baseline(repo: Path, wt: Path) -> None:
    """用户现在没提交的改动（含已暂存的）和没被忽略的新文件，按字节原样放进临时目录。
    不用 git diff | git apply：开着 autocrlf 时两边换行符不一样，补丁会对不上"""
    changed = [p for p in git(repo, "diff", "HEAD", "--name-only", "-z", "--no-renames").split("\0") if p]
    untracked = [p for p in git(repo, "ls-files", "-z", "--others", "--exclude-standard").split("\0") if p]
    if len(changed) + len(untracked) > MAX_UNTRACKED_FILES:
        raise FixError(f"仓库里没提交的改动太多（{len(changed) + len(untracked)} 个文件），先提交一下再来。")
    total = 0
    for rel in changed + untracked:
        src, dst = repo / rel, wt / rel
        if not src.exists():  # 用户删掉了这个文件
            dst.unlink(missing_ok=True)
            continue
        if not src.is_file():
            continue
        total += src.stat().st_size
        if total > MAX_UNTRACKED_BYTES:
            raise FixError("没提交的改动太大了，先提交一下再来。")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def link_dir(src: Path, dst: Path) -> bool:
    """依赖目录（node_modules、.venv）不在 Git 里：在临时目录里做个链接指向原来的，不用重新安装。
    Windows 上用目录联接（不要管理员权限）。删临时目录前一定先用 remove_link 拆掉链接"""
    if not src.is_dir() or dst.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    if WIN:
        import _winapi

        _winapi.CreateJunction(str(src), str(dst))
    else:
        os.symlink(src, dst, target_is_directory=True)
    return True


def run_test(wt: Path, cmd: TestCommand, timeout: float = TEST_TIMEOUT) -> TestRun:
    t0 = time.perf_counter()
    env = {**os.environ, "CI": "1", "PYTHONDONTWRITEBYTECODE": "1", "FORCE_COLOR": "0", "NO_COLOR": "1"}
    try:
        proc = subprocess.Popen(
            cmd.argv, cwd=wt / cmd.cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
            creationflags=NO_WINDOW | (subprocess.CREATE_NEW_PROCESS_GROUP if WIN else 0),
            start_new_session=not WIN,
        )
    except OSError as err:
        return TestRun(cmd.label, False, 0.0, f"测试命令启动不了：{err}")
    try:
        out, _ = proc.communicate(timeout=timeout)
        ok = proc.returncode == 0
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        out, _ = proc.communicate()
        ok = False
        out = (out or b"") + f"\n（测试超过 {timeout:.0f} 秒没跑完，停掉了）".encode()
    text = (out or b"").decode("utf-8", "replace").replace("\r\n", "\n")
    return TestRun(cmd.label, ok, round(time.perf_counter() - t0, 1), text[-OUTPUT_TAIL:].strip())


def remove_link(link: Path) -> None:
    _remove_link(link)


def atomic_write(path: Path, data: bytes) -> None:
    _atomic_write(path, data)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".aila-tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _remove_link(link: Path) -> None:
    """只删链接本身，不碰它指向的目录"""
    try:
        if os.path.isjunction(link):
            os.rmdir(link)
        elif link.is_symlink():
            link.unlink()
    except OSError:
        pass


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    if WIN:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, creationflags=NO_WINDOW)
    else:
        try:
            os.killpg(proc.pid, 9)
        except OSError:
            proc.kill()


def fix_stats(log_path: Path) -> dict[str, int]:
    """修了几次、测试通过几次、改进用户代码几次"""
    if not log_path.exists():
        return {"attempts": 0, "tested": 0, "passed": 0, "merged": 0}
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    tested = [r for r in rows if r["tests"]]
    return {
        "attempts": len(rows),
        "tested": len(tested),
        "passed": sum(r["passed"] for r in tested),
        "merged": sum(r["merged"] for r in rows),
    }
