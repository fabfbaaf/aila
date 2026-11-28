"""把同基线的子任务放进独立工作区验收，收回时核对通过测试的那一版。"""

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any

from ..code.fix import MAX_UNTRACKED_BYTES, TestCommand, TestRun, atomic_write, detect_tests, git, run_test
from .branch import MAX_FILES, BranchInfo, _at, _same, changes, discard, open_branch
from .workspace import Workspace


MAX_COLLABORATORS = 4


class CollaborationError(Exception):
    """在主任务里显示的联合验收问题。"""


@dataclass(frozen=True)
class Assignment:
    agent: str
    title: str
    task: str
    paths: tuple[str, ...]
    model: str = ""


def _text(values: dict[str, Any], name: str, label: str, limit: int) -> str:
    value = values.get(name)
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
        raise CollaborationError(f"写清楚{label}，最多 {limit} 字。")
    return value.strip()


def relative_path(raw: str) -> str:
    raw = raw.strip().replace("\\", "/")
    path = PurePosixPath(raw)
    if (not path.parts or path.is_absolute() or ":" in raw or any(c in raw for c in "\r\n\x00*?[]")
            or any(part == ".." or part.casefold() == ".git" for part in path.parts)
            or Workspace.is_secret(Path(raw))):
        raise CollaborationError(f"要用仓库里的普通文件或目录：{raw}")
    return path.as_posix()


def owns(scope: str, path: str) -> bool:
    scope, path = scope.casefold(), path.casefold()
    return path == scope or path.startswith(scope + "/")


@dataclass(frozen=True)
class PairPlan:
    contract: str
    acceptance: str
    test_file: str
    assignments: tuple[Assignment, ...]

    @staticmethod
    def parse(args: dict[str, Any], engines: set[str], count: int = 2) -> "PairPlan":
        contract = _text(args, "contract", "协作者共用的接口约定", 12000)
        acceptance = _text(args, "acceptance", "要验证的用户操作和预期结果", 4000)
        test_file = relative_path(_text(args, "test_file", "联合验收的测试文件", 500))
        raw = args.get("assignments")
        if not isinstance(raw, list) or len(raw) != count:
            raise CollaborationError(f"要给每个协作者安排一项工作，共 {count} 项。")
        tasks = []
        for item in raw:
            if not isinstance(item, dict) or not isinstance(item.get("agent"), str) or item["agent"] not in engines:
                raise CollaborationError("每项工作要选一个已支持的本机 Agent。")
            paths = item.get("paths")
            if not isinstance(paths, list) or not 1 <= len(paths) <= 30 or not all(isinstance(p, str) for p in paths):
                raise CollaborationError("每项工作要列出负责的目录或文件。")
            model = item.get("model", "")
            if not isinstance(model, str) or len(model) > 200 or any(ord(c) < 32 for c in model):
                raise CollaborationError("模型编号无效。")
            tasks.append(Assignment(item["agent"], _text(item, "title", "任务名", 40),
                                    _text(item, "task", "任务内容", 8000), tuple(relative_path(p) for p in paths), model))
        scopes: list[str] = []
        for assignment in tasks:
            for path in assignment.paths:
                overlap = next((scope for scope in scopes if owns(path, scope) or owns(scope, path)), None)
                if overlap:
                    raise CollaborationError(f"负责范围重叠：{path} 和 {overlap}。把共享文件交给同一个人。")
            scopes.extend(assignment.paths)
        return PairPlan(contract, acceptance, test_file, tuple(tasks))


def _regular_path(root: Path, rel: str) -> Path:
    path = root / relative_path(rel)
    if path.resolve() != path or (path.exists() and not path.is_file()):
        raise CollaborationError(f"联合验收只能处理普通文件：{rel}")
    return path


def tree(info: BranchInfo) -> str:
    changes(info)
    return git(Path(info.worktree), "write-tree").strip()


def prepare_candidate(stage: BranchInfo, base: str, members: list[tuple[BranchInfo, list[str], str]]) -> list[str]:
    root = Path(stage.worktree)
    files: dict[str, tuple[str, Path, str]] = {}
    size = 0
    for info, scope, title in members:
        if info.base != base or info.repo != stage.repo:
            raise CollaborationError(f"「{title}」的工作区已经换了基线。")
        changed = changes(info)
        if not changed:
            raise CollaborationError(f"「{title}」还没有改动。")
        for status, rel in changed:
            if status not in ("A", "M", "D") or not any(owns(p, rel) for p in scope):
                raise CollaborationError(f"「{title}」改了负责范围以外或无法合并的文件：{rel}")
            if rel.casefold() in files:
                raise CollaborationError(f"多项工作都改了 {rel}。")
            src = _regular_path(Path(info.worktree), rel)
            dest = _regular_path(root, rel)
            before = dest.read_bytes() if dest.is_file() else None
            if not _same(before, _at(Path(info.worktree), info.base, rel)):
                raise CollaborationError(f"主任务和「{title}」都改了 {rel}，先处理冲突。")
            size += len(before or b"")
            if status != "D":
                size += src.stat().st_size
            files[rel.casefold()] = (status, src, rel)
    if len(files) > MAX_FILES or size > MAX_UNTRACKED_BYTES:
        raise CollaborationError("协作改动太多或太大，先拆成更小的任务。")
    for status, src, rel in files.values():
        dest = _regular_path(root, rel)
        if status == "D":
            dest.unlink(missing_ok=True)
        else:
            atomic_write(dest, src.read_bytes())
    return sorted(rel for _, _, rel in files.values())


def _acceptance_command(root: Path, test_file: str) -> TestCommand:
    test = _regular_path(root, test_file)
    if not test.is_file():
        raise CollaborationError(f"验收测试文件不存在：{test_file}")
    tests = detect_tests(root, [test_file])
    if not tests or tests[0].kind not in ("python", "node"):
        raise CollaborationError("验收文件要放在能识别 pytest 或 npm test 的项目里。")
    cmd = tests[0]
    if cmd.kind == "node":
        script = json.loads((root / cmd.cwd / "package.json").read_text(encoding="utf-8"))["scripts"]["test"]
        if not re.fullmatch(r"(?:vitest\s+run|jest|node\s+--test)(?:\s+[^;&|<>\r\n]+)?", script.strip()):
            raise CollaborationError("指定 Node 验收文件需要 vitest run、jest 或 node --test 作为项目的 test 脚本。")
    path = test.relative_to(root / cmd.cwd).as_posix()
    return replace(cmd, argv=[*cmd.argv, "--", f"./{path}"], label=f"联合验收（{test_file}）")


def check_candidate(stage: BranchInfo, files: list[str], test_file: str) -> tuple[str, list[TestRun]]:
    root = Path(stage.worktree)
    expected = tree(stage)
    acceptance = _acceptance_command(root, test_file)
    results = []
    for cmd in [acceptance, *detect_tests(root, files)]:
        result = run_test(root, cmd)
        results.append(result)
        if not result.ok:
            break
    if tree(stage) != expected:
        raise CollaborationError("测试运行时写改了候选代码，先处理这些改动再验收。")
    return expected, results


def apply_candidate(stage: BranchInfo, expected: str, work_dir: Path) -> None:
    root, target = Path(stage.worktree), Path(stage.repo)
    if tree(stage) != expected:
        raise CollaborationError("验收后的改动已经变了，重新跑联合验收。")
    fresh = open_branch(target, work_dir)
    try:
        if git(Path(fresh.worktree), "rev-parse", f"{fresh.base}^{{tree}}").strip() != (
            git(root, "rev-parse", f"{stage.base}^{{tree}}").strip()
        ):
            raise CollaborationError("主任务的代码已经变了，用最新代码重新派工和验收。")
    finally:
        discard(fresh)

    ready: list[tuple[Path, bytes | None, bytes | None]] = []
    for status, rel in changes(stage):
        dest = _regular_path(target, rel)
        before = dest.read_bytes() if dest.is_file() else None
        if not _same(before, _at(root, stage.base, rel)):
            raise CollaborationError(f"主任务的 {rel} 已经变了，重新跑联合验收。")
        after = None if status == "D" else _regular_path(root, rel).read_bytes()
        ready.append((dest, before, after))
    if tree(stage) != expected:
        raise CollaborationError("验收后的改动已经变了，重新跑联合验收。")
    written = []
    try:
        for dest, before, after in ready:
            if (dest.read_bytes() if dest.is_file() else None) != before:
                raise CollaborationError(f"应用时 {dest.name} 被改了，重新跑联合验收。")
            if after is None:
                dest.unlink()
            else:
                atomic_write(dest, after)
            written.append((dest, before))
    except (OSError, CollaborationError):
        for dest, before in reversed(written):
            if before is None:
                dest.unlink(missing_ok=True)
            else:
                atomic_write(dest, before)
        raise
