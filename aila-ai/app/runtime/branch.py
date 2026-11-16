"""「在新分支上做」：她在一个 git worktree 里改，你的工作区一个字都不动；做完你说合并，才跑测试、合并回来。

- 开分支：从 HEAD 开一个 aila/task-xxxx 分支，放进你现在没提交的改动（基线，按字节复制，和修代码一样）；
  依赖目录（node_modules、.venv）做链接，不用重新安装，她在里面也能直接跑测试
- 合并：先跑项目自带的测试（命令按改了哪些文件自动识别，不由大模型决定），全过才合并；
  合并前确认这期间你没改过同样的文件（和基线比），有冲突就不合并、告诉你是哪些；
  合并 = 把改过的文件按字节写回你的工作区（删了的删掉），不碰你的 git 历史；合并后分支上记一次提交当新的基线，
  她可以接着在分支上做，下次只合并新的改动
- 放弃：删掉分支和临时目录，你的代码保持原样
"""

import secrets
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..code.fix import (
    AILA_GIT,
    FixError,
    TestRun,
    atomic_write,
    copy_baseline,
    detect_tests,
    git,
    link_dir,
    remove_link,
    repo_root,
    run_test,
)

DEP_DIRS = ("node_modules", ".venv", "venv")
MAX_FILES = 200


class BranchError(Exception):
    """说给用户听的错误"""


@dataclass
class BranchInfo:
    repo: str  # 你的仓库
    worktree: str  # 她工作的临时目录
    branch: str
    base: str  # 上次合并（或者开分支时）的提交：和它比出这次要合并的改动
    sub: str = ""  # 会话目录在仓库里的子目录（会话开在仓库根目录时为空）
    links: list[str] = field(default_factory=list)

    @property
    def workdir(self) -> Path:
        return Path(self.worktree) / self.sub if self.sub else Path(self.worktree)

    @staticmethod
    def of(data: dict[str, Any]) -> "BranchInfo":
        return BranchInfo(**{k: data[k] for k in ("repo", "worktree", "branch", "base", "sub", "links") if k in data})


@dataclass
class MergeReport:
    merged: bool
    reason: str
    files: list[str] = field(default_factory=list)
    tests: list[TestRun] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    base: str = ""
    seconds: float = 0.0

    def as_event(self) -> dict[str, Any]:
        data = asdict(self)
        for t in data["tests"]:
            t["tail"] = t["tail"][-1500:]
        return {"type": "merge", **data}


def open_branch(root: Path, work_dir: Path) -> BranchInfo:
    try:
        repo = repo_root(root)
    except FixError as err:
        raise BranchError("这个文件夹不在 git 仓库里，没法开新分支。") from err
    if not git(repo, "rev-parse", "--verify", "-q", "HEAD", check=False).strip():
        raise BranchError("这个仓库还没有任何提交，没法开新分支。先提交一次。")
    bid = secrets.token_hex(3)
    branch = f"aila/task-{bid}"
    wt = work_dir / f"task-{bid}"
    work_dir.mkdir(parents=True, exist_ok=True)
    git(repo, "worktree", "add", "-q", "-b", branch, str(wt), "HEAD")
    try:
        copy_baseline(repo, wt)
        git(wt, *AILA_GIT, "add", "-A")
        git(wt, *AILA_GIT, "commit", "-q", "--no-verify", "--allow-empty", "-m", "艾拉：基线（你当时还没提交的改动）")
    except FixError as err:
        _remove(repo, wt, branch, [])
        raise BranchError(str(err)) from err
    links = []
    for d in [repo, *(p for p in repo.iterdir() if p.is_dir() and p.name not in (".git", *DEP_DIRS))]:
        for name in DEP_DIRS:
            dst = wt / d.relative_to(repo) / name
            if link_dir(d / name, dst):
                links.append(str(dst))
    base = git(wt, "rev-parse", "HEAD").strip()
    sub = root.resolve().relative_to(repo).as_posix() if root.resolve() != repo else ""
    return BranchInfo(str(repo), str(wt), branch, base, "" if sub == "." else sub, links)


def changes(info: BranchInfo) -> list[tuple[str, str]]:
    """分支上相对基线的改动：（A / M / D，路径）。依赖链接不算"""
    wt = Path(info.worktree)
    excludes = [f":(exclude){Path(link).relative_to(wt).as_posix()}" for link in info.links]
    git(wt, "add", "-A", "--", ".", *excludes)
    out = git(wt, "diff", "--cached", "--name-status", "--no-renames", "-z", info.base)
    parts = [p for p in out.split("\0") if p]
    return [(parts[i][0], parts[i + 1]) for i in range(0, len(parts) - 1, 2)]


def merge(info: BranchInfo, run_tests: bool = True, timeout: float = 600) -> MergeReport:
    t0 = time.perf_counter()
    repo, wt = Path(info.repo), Path(info.worktree)
    report = MergeReport(False, "", base=info.base)
    try:
        changed = changes(info)
        report.files = [p for _, p in changed]
        if not changed:
            report.reason = "分支上还没有新的改动。"
            return report
        if len(changed) > MAX_FILES:
            report.reason = f"改了 {len(changed)} 个文件，太多了，没有自动合并。分支 {info.branch} 留着，你可以自己看。"
            return report

        if run_tests:
            tests = detect_tests(repo, [p for s, p in changed if s != "D"] or report.files)
            if not tests:
                report.reason = "没找到这个项目的测试命令，没法自动验证。看过改动觉得没问题的话，可以选「不跑测试直接合并」。"
                return report
            for cmd in tests:
                result = run_test(wt, cmd, timeout)
                report.tests.append(result)
                if not result.ok:
                    report.reason = f"测试没通过（{result.label}），没有合并。改动还在分支上，可以让她接着改。"
                    return report

        # 这期间你改过同样的文件：不合并，免得覆盖你的改动（只比内容，不管换行符：git 存的和你磁盘上的换行符可能不一样）
        for status, path in changed:
            before = _at(wt, info.base, path)
            now = (repo / path).read_bytes() if (repo / path).is_file() else None
            if not _same(now, before):
                report.conflicts.append(path)
        if report.conflicts:
            report.reason = "你在这期间也改了这些文件，为了不覆盖你的改动，没有合并：" + "、".join(report.conflicts[:10])
            return report

        for status, path in changed:
            target = repo / path
            if status == "D":
                target.unlink(missing_ok=True)
            else:
                atomic_write(target, (wt / path).read_bytes())
        git(wt, *AILA_GIT, "commit", "-q", "--no-verify", "-m", f"艾拉：任务改动（{', '.join(report.files)[:150]}）")
        report.base = git(wt, "rev-parse", "HEAD").strip()
        report.merged = True
        tested = "测试通过，" if run_tests else "没跑测试，"
        report.reason = f"{tested}已经把 {len(changed)} 个文件合并到你的代码里（没有提交，你看过再提交）。"
        return report
    except FixError as err:
        report.reason = f"合并出错了：{err}"
        return report
    finally:
        report.seconds = round(time.perf_counter() - t0, 1)


def discard(info: BranchInfo) -> None:
    _remove(Path(info.repo), Path(info.worktree), info.branch, info.links)


def _remove(repo: Path, wt: Path, branch: str, links: list[str]) -> None:
    for link in links:  # 先拆链接，删临时目录时绝不能顺着链接删到原来的依赖
        remove_link(Path(link))
    git(repo, "worktree", "remove", "--force", str(wt), check=False)
    shutil.rmtree(wt, ignore_errors=True)
    git(repo, "worktree", "prune", check=False)
    git(repo, "branch", "-D", branch, check=False)


def _same(a: bytes | None, b: bytes | None) -> bool:
    if a is None or b is None:
        return a is b
    return a.replace(b"\r\n", b"\n") == b.replace(b"\r\n", b"\n")


def _at(wt: Path, commit: str, path: str) -> bytes | None:
    """某个提交里这个文件的内容（按字节）；那时还没有这个文件返回 None"""
    import subprocess

    from ..code.fix import NO_WINDOW

    out = subprocess.run(["git", "show", f"{commit}:{path}"], cwd=wt, capture_output=True, creationflags=NO_WINDOW)
    return out.stdout if out.returncode == 0 else None
