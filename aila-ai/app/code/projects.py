"""用户登记的代码项目（可以有好几个），每个项目一个索引文件（data/code/<id>.sqlite3）。

建索引在后台线程里做；用工具查之前顺手做一次增量更新（只看改过的文件，通常几十毫秒），
所以用户改了代码，她查到的就是新的。
"""

import hashlib
import json
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..memory.embedder import Embedder
from .fix import FixRunner
from .index import CodeIndex, RefreshStats
from .ignore import clean
from .ignore import read_file as read_ignore_file

STALE_SECONDS = 20.0
CHURN_COMMITS = 300  # 「按改动」看最近多少次提交：够看出热点，又不会在大仓库里慢
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


@dataclass
class Project:
    id: str
    name: str
    root: str
    addedAt: str
    # 「项目」页里加的忽略（文件夹、文件、通配符），和项目里的 .ailaignore 合起来用
    ignore: list[str] = field(default_factory=list)


class CodeProjects:
    def __init__(self, data_dir: Path, embedder: Callable[[], Embedder | None]) -> None:
        self._dir = data_dir / "code"
        self._file = self._dir / "projects.json"
        self._embedder = embedder
        self._lock = threading.Lock()
        self._indexes: dict[str, CodeIndex] = {}
        self._checked: dict[str, float] = {}
        self._embedding: dict[str, threading.Thread] = {}  # 后台补向量的线程
        self.status: dict[str, dict[str, Any]] = {}  # 正在建索引 / 好了 / 出错，给设置界面看
        self.fix_log = self._dir / "fixes.jsonl"
        self.fixer = FixRunner(self.fix_log)

    def all(self) -> list[Project]:
        if not self._file.exists():
            return []
        try:
            return [Project(**p) for p in json.loads(self._file.read_text(encoding="utf-8"))]
        except (ValueError, TypeError):
            return []

    def add(self, root: str) -> Project:
        path = Path(root).expanduser().resolve()
        if not path.is_dir():
            raise ValueError("这个文件夹不存在")
        with self._lock:
            projects = self.all()
            for p in projects:
                if Path(p.root) == path:
                    return p
            project = Project(hashlib.sha1(str(path).lower().encode()).hexdigest()[:12], path.name or str(path), str(path),
                              datetime.now(timezone.utc).isoformat())
            self._save([*projects, project])
        return project

    def remove(self, project_id: str) -> bool:
        with self._lock:
            projects = self.all()
            kept = [p for p in projects if p.id != project_id]
            if len(kept) == len(projects):
                return False
            self._save(kept)
            index = self._indexes.pop(project_id, None)
            self.status.pop(project_id, None)
        if index is not None:
            index.close()
        (self._dir / f"{project_id}.sqlite3").unlink(missing_ok=True)
        return True

    def _ignore_of(self, project_id: str) -> list[str]:
        project = self.get(project_id)
        return project.ignore if project else []

    def set_ignore(self, project_id: str, patterns: list[str]) -> Project:
        """改这个项目的忽略；下次刷新（马上在后台刷）时忽略掉的文件从索引里清出去"""
        with self._lock:
            projects = self.all()
            project = next((p for p in projects if p.id == project_id), None)
            if project is None:
                raise KeyError(project_id)
            project.ignore = clean(patterns)
            self._save(projects)
            self._checked.pop(project_id, None)  # 下次用到时马上重新看一遍文件
        return project

    def get(self, project_id: str) -> Project | None:
        return next((p for p in self.all() if p.id == project_id), None)

    def index(self, project_id: str) -> CodeIndex:
        with self._lock:
            if project_id not in self._indexes:
                project = next((p for p in self.all() if p.id == project_id), None)
                if project is None:
                    raise KeyError(project_id)
                self._indexes[project_id] = CodeIndex(Path(project.root), self._dir / f"{project_id}.sqlite3", self._embedder,
                                                      lambda pid=project_id: self._ignore_of(pid))
            return self._indexes[project_id]

    def refresh(self, project_id: str) -> RefreshStats:
        """完整更新：先结构，再补向量（登记项目、点「重新扫描」时在后台跑）"""
        self.status[project_id] = {"state": "indexing"}
        try:
            index = self.index(project_id)
            stats = index.refresh(embed=False)
            self._checked[project_id] = time.monotonic()
            embedded = index.embed_missing()
        except Exception as err:
            self.status[project_id] = {"state": "error", "error": f"{type(err).__name__}: {err}"}
            raise
        stats = RefreshStats(stats.files, stats.chunks, stats.parsed, stats.removed, embedded,
                             round(stats.seconds, 2))
        self.status[project_id] = {"state": "ready", "last": asdict(stats)}
        return stats

    def fresh(self, project_id: str) -> CodeIndex:
        """这个项目的索引，太久没看的先增量更新结构（只看改过的文件，通常很快），向量放到后台补。
        星图、按报错定位只要结构，不用等算向量（在线程里调用）"""
        if self.status.get(project_id, {}).get("state") != "indexing" and \
                time.monotonic() - self._checked.get(project_id, 0.0) > STALE_SECONDS:
            index = self.index(project_id)
            try:
                stats = index.refresh(embed=False)
                self._checked[project_id] = time.monotonic()
                if stats.parsed:
                    self._embed_later(project_id, index)
            except Exception:
                pass  # 更新失败就用旧的索引
        return self.index(project_id)

    def _embed_later(self, project_id: str, index: CodeIndex) -> None:
        with self._lock:
            running = self._embedding.get(project_id)
            if running is not None and running.is_alive():
                return  # 已经在补了，补的时候会顺带补上新的
            worker = threading.Thread(target=index.embed_missing, name=f"aila-embed-{project_id}", daemon=True)
            self._embedding[project_id] = worker
        worker.start()

    def fresh_indexes(self) -> list[tuple[Project, CodeIndex]]:
        """所有项目的索引（给工具用）"""
        return [(p, self.fresh(p.id)) for p in self.all()]

    def modified_files(self, project_id: str) -> set[str]:
        """还没提交的改动和新文件（相对项目根目录）：星图上亮一点"""
        project = self.get(project_id)
        if project is None:
            return set()
        root = Path(project.root)
        try:
            top = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"], capture_output=True,
                                 timeout=10, creationflags=NO_WINDOW).stdout.decode("utf-8", "replace").strip()
            if not top:
                return set()
            out = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "-z", "--untracked-files=all"],
                                 capture_output=True, timeout=30, creationflags=NO_WINDOW).stdout.decode("utf-8", "replace")
        except (OSError, subprocess.SubprocessError):
            return set()
        prefix = Path(root).resolve().relative_to(Path(top).resolve()).as_posix()
        prefix = "" if prefix == "." else prefix + "/"
        paths = {entry[3:] for entry in out.split("\0") if len(entry) > 3}
        return {p[len(prefix):] for p in paths if p.startswith(prefix)}

    def churn(self, project_id: str, commits: int = CHURN_COMMITS) -> dict[str, int]:
        """最近 commits 次提交里每个文件被改过几次（相对项目根目录）：星图「按改动」上色，越常改越热。
        不是 git 仓库、git 出错都返回空的（星图照样画，只是没有热度）"""
        project = self.get(project_id)
        if project is None:
            return {}
        try:
            out = subprocess.run(
                ["git", "-C", str(project.root), "-c", "core.quotepath=false", "log", f"-n{commits}",
                 "--relative", "--name-only", "--format=", "--no-renames"],
                capture_output=True, timeout=30, creationflags=NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError):
            return {}
        if out.returncode != 0:
            return {}
        counts: dict[str, int] = {}
        for line in out.stdout.decode("utf-8", "replace").splitlines():
            if line.strip():
                counts[line.strip()] = counts.get(line.strip(), 0) + 1
        return counts

    def describe(self) -> list[dict[str, Any]]:
        out = []
        for p in self.all():
            try:
                stats = self.index(p.id).stats()
            except Exception:
                stats = {}
            out.append({**asdict(p), "ignoreFile": read_ignore_file(Path(p.root)), **stats,
                        **self.status.get(p.id, {"state": "ready" if stats.get("files") else "new"})})
        return out

    def _save(self, projects: list[Project]) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(p) for p in projects], ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self._file)
