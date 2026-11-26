"""一个项目的代码索引：本机 SQLite（函数块 + 全文检索 + 向量 + 调用关系），代码不离开这台电脑。

- 增量更新：先比修改时间和大小，变了再比内容哈希，只重新解析、重新算向量变了的文件；删掉的文件清出去
- 检索三路合并（RRF）：名字完全对上（run_agent、SyncService.putMemory）、关键词（FTS5 trigram，
  注释是中文也能搜）、向量（懂意思，「哪里检查请求来源」能找到 is_local）
- 按报错定位：从堆栈里找出文件和行号，直接落到包含那一行的函数块；再沿调用图找谁调用了它、它又调用了谁
"""

import hashlib
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..knowledge.store import _fts_query
from ..memory.embedder import Embedder
from ..runtime.workspace import Workspace, WorkspaceError
from .endpoints import Endpoint, connections, is_test, link, scan
from .ignore import clean, ignored
from .imports import resolve as resolve_imports
from .imports import scan_imports
from .ignore import read_file as read_ignore_file
from .parse import MAX_FILE_BYTES, Chunk, language_of, parse_file
from .stack import Frame, parse_frames

IGNORE_DIRS = {
    ".git", "node_modules", ".venv", "venv", "env", "__pycache__", "dist", "build", "out", "target", ".idea",
    ".vscode", ".next", ".nuxt", "coverage", ".pytest_cache", ".mypy_cache", ".gradle", "site-packages", "vendor",
}
MAX_FILES = 8000
MAX_LIBS = 30  # 星图外圈最多画几个第三方库（按用到的文件数排）
CANDIDATES = 30
RRF_K = 60
EMBED_TEXT_CHARS = 500
KIND_ORDER = {"function": 0, "method": 0, "class": 1, "module": 2}

SCHEMA = """
create table if not exists files (
    path  text primary key,
    lang  text not null,
    sha1  text not null,
    mtime real not null,
    size  integer not null,
    lines integer not null
);
create table if not exists chunks (
    id         integer primary key,
    file       text not null references files (path) on delete cascade,
    kind       text not null,
    name       text not null,
    qname      text not null,
    start_line integer not null,
    end_line   integer not null,
    signature  text not null,
    doc        text not null,
    body       text not null,
    embedding  blob
);
create index if not exists chunks_file on chunks (file);
create index if not exists chunks_name on chunks (name);
create table if not exists calls (
    chunk_id integer not null references chunks (id) on delete cascade,
    callee   text not null
);
create index if not exists calls_callee on calls (callee);
create index if not exists calls_chunk on calls (chunk_id);
create virtual table if not exists chunks_fts using fts5 (body, tokenize = 'trigram');
create virtual table if not exists files_fts using fts5 (path unindexed, body, tokenize = 'trigram');
create table if not exists meta (key text primary key, value text not null);
"""

COLUMNS = "id, file, kind, name, qname, start_line, end_line, signature, doc, body"


@dataclass(frozen=True)
class CodeChunk:
    id: int
    file: str
    kind: str
    name: str
    qname: str
    start: int
    end: int
    signature: str
    doc: str
    text: str

    @property
    def location(self) -> str:
        return f"{self.file}:{self.start}-{self.end}"


@dataclass(frozen=True)
class RefreshStats:
    files: int
    chunks: int
    parsed: int  # 这次重新解析了几个文件
    removed: int
    embedded: int
    seconds: float


@dataclass(frozen=True)
class Located:
    frame: Frame
    file: str | None  # 在项目里对应的文件（找不到就是库代码或别的项目）
    chunk: CodeChunk | None


# 对象上的方法调用（.append()、.get()）太常见，按名字连不出靠谱的调用关系
COMMON_METHODS = {
    "append", "extend", "get", "set", "add", "pop", "push", "map", "filter", "reduce", "join", "split", "close", "clear",
    "keys", "values", "items", "update", "remove", "delete", "send", "emit", "then", "catch", "log", "toString",
    "equals", "hashCode", "length", "size", "stream", "collect", "forEach", "replace", "strip", "lower", "upper",
    "format", "read", "write", "open", "run", "start", "stop", "wait", "next", "copy", "sort", "find", "index",
    "count", "has", "includes", "slice", "indexOf", "trim", "parse", "stringify", "json", "decode", "encode",
    "execute", "fetchone", "fetchall", "call", "apply", "bind", "init", "load", "save", "reset", "list", "info",
    "warning", "error", "debug", "exception", "isEmpty", "contains", "put", "of", "toList", "getString",
}


FAMILIES = {"python": "python", "java": "java", "javascript": "web", "typescript": "web", "tsx": "web", "vue": "web"}


def family(path: str) -> str:
    """按名字连调用关系只在同一类语言里连：Python 里的 Cart() 不会是 Vue 的 Cart 组件"""
    return FAMILIES.get(language_of(path) or "", "")


def embed_text(rel: str, c: Chunk) -> str:
    """算向量用的文字：文件、所在文件和类是干什么的、名字、签名、注释、开头一段代码"""
    return f"{rel}\n{c.context}\n{c.qname}\n{c.signature}\n{c.doc}\n{c.text[:EMBED_TEXT_CHARS]}"


def code_fts_query(query: str) -> str:
    """在资料库的查询拆法上，给英文单词加上去掉词尾的形式：rotated 也能找到 rotate"""
    base = _fts_query(query)
    stems = []
    for word in re.findall(r"[A-Za-z]{5,}", query):
        stem = re.sub(r"(?:ing|ed|es|s|ly)$", "", word.lower())
        if len(stem) >= 4 and stem != word.lower():
            stems.append('"' + stem + '"')
    return " OR ".join(p for p in [base, *dict.fromkeys(stems)] if p)


class CodeIndex:
    def __init__(self, root: Path, db_path: Path | str, embedder: Callable[[], Embedder | None],
                 ignore: Callable[[], list[str]] = list, *, ignore_root: Path | None = None) -> None:
        self._workspace = Workspace(root)
        self.root = self._workspace.root
        self._ignore_root = (ignore_root or self.root).resolve()
        self._ignore = ignore  # 「项目」页里给这个项目加的忽略（和项目里的 .ailaignore 合起来用）
        if isinstance(db_path, Path):
            db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.execute("pragma foreign_keys = on")
        self._db.executescript(SCHEMA)
        # 旧的索引文件没有 context 列（所在文件和类的说明，后台补算向量时要用）：补上
        if "context" not in {r[1] for r in self._db.execute("pragma table_info(chunks)")}:
            self._db.execute("alter table chunks add column context text not null default ''")
        self._get_embedder = embedder
        self._lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._vectors: tuple[np.ndarray, list[int]] | None = None
        self._paths: list[str] | None = None
        self._endpoints: dict[str, tuple[float, int, list[Endpoint], list[str]]] = {}

    # ---------- 建索引 ----------

    def list_files(self) -> list[str]:
        """项目里的源码文件（相对路径，/ 分隔）。是 Git 仓库就按 Git 的忽略规则来"""
        files = self._git_files()
        if files is None:
            files = []
            for dirpath, dirnames, filenames in os.walk(self.root):
                dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".")]
                for name in filenames:
                    rel = os.path.relpath(os.path.join(dirpath, name), self.root).replace(os.sep, "/")
                    files.append(rel)
        patterns = clean([*self._ignore(), *read_ignore_file(self._ignore_root)])
        prefix = self.root.relative_to(self._ignore_root)
        local = read_ignore_file(self.root) if self._ignore_root != self.root else []
        picked = [f for f in files if language_of(f) and not any(part in IGNORE_DIRS for part in f.split("/")[:-1])
                  and not ignored((prefix / f).as_posix(), patterns) and not ignored(f, local)]
        return sorted(picked)[:MAX_FILES]

    def _git_files(self) -> list[str] | None:
        try:
            out = subprocess.run(
                ["git", "-C", str(self.root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                capture_output=True, timeout=30, check=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        return [p for p in out.decode("utf-8", errors="replace").split("\0") if p]

    def refresh(self, embed: bool = True) -> RefreshStats:
        """增量更新索引。同一时间只跑一个（后到的等前一个跑完再看一遍，基本没什么要做的）

        embed=False：只更新结构（切块、调用关系、关键词），向量留空，之后用 embed_missing 补。
        星图、按报错定位只要结构，不用等最慢的算向量那一步
        """
        with self._refresh_lock:
            return self._refresh(embed)

    @contextmanager
    def refreshed_structure(self) -> Iterator[None]:
        """多次读取调用图和位置时，共用一版结构。"""
        with self._refresh_lock:
            self._refresh(embed=False, references=True)
            yield

    def embed_missing(self, batch: int = 128) -> int:
        """给还没有向量的块补算向量（后台慢慢做），返回补了几块"""
        embedder = self._get_embedder()
        if embedder is None:
            return 0
        done = 0
        while True:
            with self._lock:
                rows = self._db.execute(
                    "select id, file, context, qname, signature, doc, body from chunks where embedding is null limit ?", (batch,)
                ).fetchall()
            if not rows:
                break
            texts = [f"{f}\n{ctx}\n{q}\n{sig}\n{doc}\n{body[:EMBED_TEXT_CHARS]}" for _, f, ctx, q, sig, doc, body in rows]
            vectors = embedder.embed(texts)  # 不占着锁
            with self._lock, self._db:
                self._db.executemany(
                    "update chunks set embedding = ? where id = ? and embedding is null",
                    [(vectors[i].astype(np.float32).tobytes(), r[0]) for i, r in enumerate(rows)],
                )
                self._vectors = None
            done += len(rows)
        return done

    def _refresh(self, embed: bool = True, *, references: bool = False) -> RefreshStats:
        t0 = time.perf_counter()
        files = self.list_files()
        with self._lock:
            known = {r[0]: (r[1], r[2], r[3]) for r in self._db.execute("select path, sha1, mtime, size from files")}
            indexed = {r[0] for r in self._db.execute("select rowid from files_fts")} if references else set()
            row_ids = dict(self._db.execute("select path, rowid from files")) if references else {}

        todo: list[tuple[str, bytes, str, float, int]] = []
        touched: list[tuple[float, int, str]] = []
        present: set[str] = set()
        backfill: list[tuple[int, str, str]] = []
        for rel in files:
            path = self._source_path(rel)
            if path is None:
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            if st.st_size > MAX_FILE_BYTES:
                continue
            present.add(rel)
            old = known.get(rel)
            missing_reference = references and row_ids.get(rel) not in indexed
            if old and old[1] == st.st_mtime and old[2] == st.st_size and not missing_reference:
                continue  # 没改过：连读都不用读
            try:
                data = path.read_bytes()
            except OSError:
                continue
            sha = hashlib.sha1(data).hexdigest()
            if old and old[0] == sha:
                if missing_reference:
                    backfill.append((row_ids[rel], rel, data.decode("utf-8", errors="replace")))
                touched.append((st.st_mtime, st.st_size, rel))  # 只是保存了一下，内容没变
                continue
            todo.append((rel, data, sha, st.st_mtime, st.st_size))
        removed = [p for p in known if p not in present]

        parsed = [(rel, data, sha, mtime, size, parse_file(rel, data)) for rel, data, sha, mtime, size in todo]
        texts = [embed_text(rel, c) for rel, *_, chunks in parsed for c in chunks]
        embedder = self._get_embedder() if embed else None
        vectors = embedder.embed(texts) if embedder is not None and texts else None  # 最慢的一步，不占着锁

        with self._lock, self._db:
            for rel in removed:
                self._drop_file(rel)
            i = 0
            for rel, data, sha, mtime, size, chunks in parsed:
                self._drop_file(rel)
                file_row = self._db.execute(
                    "insert into files values (?, ?, ?, ?, ?, ?)",
                    (rel, language_of(rel) or "", sha, mtime, size, data.count(b"\n") + 1),
                )
                if references:
                    self._db.execute("insert into files_fts (rowid, path, body) values (?, ?, ?)",
                                     (file_row.lastrowid, rel, data.decode("utf-8", errors="replace")))
                for c in chunks:
                    blob = vectors[i].astype(np.float32).tobytes() if vectors is not None else None
                    i += 1
                    cur = self._db.execute(
                        "insert into chunks (file, kind, name, qname, start_line, end_line, signature, doc, body, embedding, context)"
                        " values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (rel, c.kind, c.name, c.qname, c.start, c.end, c.signature, c.doc, c.text, blob, c.context),
                    )
                    self._db.execute(
                        "insert into chunks_fts (rowid, body) values (?, ?)",
                        # 关键词这一路不放文件说明：否则同一个文件里的函数都一样命中，挤掉真正相关的那个（评测里试过）
                        (cur.lastrowid, f"{rel}\n{c.qname}\n{c.signature}\n{c.doc}\n{c.text}"),
                    )
                    self._db.executemany("insert into calls values (?, ?)", [(cur.lastrowid, n) for n in c.calls])
            self._db.executemany("insert into files_fts (rowid, path, body) values (?, ?, ?)", backfill)
            self._db.executemany("update files set mtime = ?, size = ? where path = ?", touched)
            self._db.execute("insert or replace into meta values ('refreshed_at', ?)", (str(time.time()),))
            if parsed or removed:
                self._vectors = None
                self._paths = None
            total_files, total_chunks = self._db.execute("select (select count(*) from files), (select count(*) from chunks)").fetchone()

        return RefreshStats(total_files, total_chunks, len(parsed), len(removed), len(texts) if vectors is not None else 0,
                            round(time.perf_counter() - t0, 2))

    def _drop_file(self, rel: str) -> None:
        ids = [(r[0],) for r in self._db.execute("select id from chunks where file = ?", (rel,))]
        self._db.executemany("delete from chunks_fts where rowid = ?", ids)
        self._db.execute("delete from files_fts where rowid in (select rowid from files where path = ?)", (rel,))
        self._db.execute("delete from files where path = ?", (rel,))  # 块和调用关系跟着删（外键级联）

    # ---------- 查 ----------

    def search(
        self, query: str, k: int = 6, modes: tuple[str, ...] = ("exact", "keyword", "vector"), drill: str | None = "after",
        *, matches: dict[int, list[str]] | None = None,
    ) -> list[CodeChunk]:
        """modes、drill 只在评测时改：看每一路单独、合起来、加不加「往文件里细找」各有多准

        往文件里细找（drill）：大问题常常先命中整个文件或类（它们的说明写的就是这件事），
        但用户要的是里面具体的函数。命中文件 / 类时，用向量在那个文件里挑一两个最像的函数，排在它旁边。
        """
        query = query.strip()
        if not query:
            return []
        ranks: dict[int, float] = {}

        def add(ids: list[int], method: str, weight: float = 1.0) -> None:
            for rank, cid in enumerate(ids):
                ranks[cid] = ranks.get(cid, 0.0) + weight / (RRF_K + rank + 1)
                if matches is not None:
                    matches.setdefault(cid, []).append(method)

        if "exact" in modes:
            add(self._exact(query), "exact", weight=2.0)  # 名字完全对上是很强的信号
        if "keyword" in modes and (fts := code_fts_query(query)):
            with self._lock:
                rows = self._db.execute(
                    "select rowid from chunks_fts where chunks_fts match ? order by bm25(chunks_fts) limit ?", (fts, CANDIDATES)
                ).fetchall()
            add([r[0] for r in rows], "keyword")
        qvec = None
        if "vector" in modes or drill:
            embedder = self._get_embedder()
            matrix, ids = self._load_vectors()
            if embedder is not None and len(ids):
                qvec = embedder.embed_query(query)
                if "vector" in modes:
                    scores = matrix @ qvec
                    add([ids[i] for i in np.argsort(-scores)[:CANDIDATES]], "vector")

        top = [cid for cid, _ in sorted(ranks.items(), key=lambda kv: -kv[1])[:k]]
        found = {c.id: c for c in self._chunks_by_id(top)}
        hits = [found[cid] for cid in top if cid in found]
        if drill and qvec is not None:
            hits = self._drill(hits, qvec, before=drill == "before")
            if matches is not None:
                for hit in hits:
                    matches.setdefault(hit.id, ["drill"])
        return hits[:k]

    def _drill(self, hits: list[CodeChunk], qvec: np.ndarray, before: bool) -> list[CodeChunk]:
        matrix, ids = self._load_vectors()
        pos = {cid: i for i, cid in enumerate(ids)}
        out: list[CodeChunk] = []
        seen = {h.id for h in hits}
        for h in hits:
            picks: list[CodeChunk] = []
            if h.kind in ("module", "class", "template"):
                with self._lock:
                    inner = [CodeChunk(*r) for r in self._db.execute(
                        f"select {COLUMNS} from chunks where file = ? and kind in ('function', 'method') "
                        "and start_line >= ? and end_line <= ?",
                        (h.file, h.start if h.kind == "class" else 0, h.end if h.kind == "class" else 1 << 30),
                    )]
                # 只按分数排（分数一样时不能去比较块本身）
                scored = sorted(
                    ((float(matrix[pos[c.id]] @ qvec), c) for c in inner if c.id in pos and c.id not in seen), key=lambda sc: sc[0]
                )
                picks = [c for _, c in reversed(scored[-2:])]
                seen.update(c.id for c in picks)
            out += [*picks, h] if before else [h, *picks]
        return out

    def _exact(self, query: str) -> list[int]:
        names = {n for n in re.findall(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*", query) if len(n) >= 3}
        if not names:
            return []
        short = {n.rsplit(".", 1)[-1] for n in names}
        marks = ",".join("?" * len(names))
        with self._lock:
            rows = self._db.execute(
                f"select id, kind, qname from chunks where qname in ({marks}) or name in ({','.join('?' * len(short))}) limit ?",
                [*names, *short, CANDIDATES],
            ).fetchall()
        rows.sort(key=lambda r: (r[2] not in names, KIND_ORDER.get(r[1], 3)))  # 带类名的完整名字优先
        return [r[0] for r in rows]

    def chunk_at(self, file: str, line: int) -> CodeChunk | None:
        """包含这一行的最内层的块（函数里定义的函数优先于外层函数；都没有才算模块顶层）"""
        with self._lock:
            row = self._db.execute(
                f"select {COLUMNS} from chunks where file = ? and start_line <= ? and end_line >= ? "
                "order by kind in ('module', 'template'), kind = 'class', end_line - start_line limit 1",
                (file, line, line),
            ).fetchone()
        return CodeChunk(*row) if row else None

    def symbol(self, name: str) -> list[CodeChunk]:
        """按名字找：完整名字（Class.method）或者函数名"""
        with self._lock:
            rows = self._db.execute(
                f"select {COLUMNS} from chunks where qname = ? or name = ? or qname like ? limit 10",
                (name, name, f"%.{name}"),
            ).fetchall()
        return [CodeChunk(*r) for r in rows]

    def _unique_method(self, name: str) -> bool:
        """对象上调用 .name() 时，只有项目里只定义了一个叫这个名字的、名字又不常见，才认为调的是它"""
        if name in COMMON_METHODS:
            return False
        row = self._db.execute("select count(*) from chunks where name = ? and kind in ('function', 'method')", (name,)).fetchone()
        return row[0] == 1

    def callers(self, chunk: CodeChunk, limit: int = 6) -> list[CodeChunk]:
        """谁调用了它（按名字；同一个文件里的优先）"""
        cols = ", ".join("c." + col for col in COLUMNS.split(", "))
        with self._lock:
            forms = [chunk.name] + (["." + chunk.name] if self._unique_method(chunk.name) else [])
            rows = self._db.execute(
                f"select distinct {cols} from calls k join chunks c on c.id = k.chunk_id "
                f"where k.callee in ({','.join('?' * len(forms))}) and c.id != ? and c.kind not in ('module', 'template') "
                "order by c.file != ?, c.file limit ?",
                (*forms, chunk.id, chunk.file, limit * 3),
            ).fetchall()
        mine = family(chunk.file)
        return [c for c in (CodeChunk(*r) for r in rows) if family(c.file) == mine][:limit]

    def callees(self, chunk: CodeChunk, limit: int = 8) -> list[CodeChunk]:
        """它调用了谁：每个名字挑一个最可能的（同一个文件 > 同一个目录 > 别处）"""
        folder = chunk.file.rsplit("/", 1)[0] if "/" in chunk.file else ""
        with self._lock:
            raw = [r[0] for r in self._db.execute("select callee from calls where chunk_id = ?", (chunk.id,))]
            names = [n for n in raw if not n.startswith(".")] + [n[1:] for n in raw if n.startswith(".") and self._unique_method(n[1:])]
            if not names:
                return []
            rows = self._db.execute(
                f"select {COLUMNS} from chunks where name in ({','.join('?' * len(names))}) "
                "and kind not in ('module', 'template') and id != ?",
                [*names, chunk.id],
            ).fetchall()
        best: dict[str, tuple[int, CodeChunk]] = {}
        mine = family(chunk.file)
        for r in rows:
            c = CodeChunk(*r)
            if family(c.file) != mine:
                continue
            score = 0 if c.file == chunk.file else 1 if folder and c.file.startswith(folder + "/") else 2
            if c.name not in best or score < best[c.name][0]:
                best[c.name] = (score, c)
        ordered = sorted(best.values(), key=lambda sc: (sc[0], names.index(sc[1].name)))
        return [c for _, c in ordered[:limit]]

    def locate(self, text: str) -> list[Located]:
        """报错文字 → 堆栈里每一帧落在项目的哪个函数上（离出错点最近的在前）"""
        return [self._locate_frame(f) for f in parse_frames(text)]

    def _locate_frame(self, frame: Frame) -> Located:
        rel = self.resolve_file(frame)
        return Located(frame, rel, self.chunk_at(rel, frame.line) if rel else None)

    def resolve_file(self, frame: Frame) -> str | None:
        paths = self._all_paths()
        if frame.java_class:  # com.aila.server.sync.SyncService → com/aila/server/sync/SyncService.java
            suffix = frame.java_class.split("$")[0].replace(".", "/") + ".java"
            return next((p for p in paths if p.endswith(suffix)), None)
        raw = frame.file.replace("\\", "/")
        root = str(self.root).replace("\\", "/").rstrip("/") + "/"
        if raw.lower().startswith(root.lower()):
            rel = raw[len(root):]
            return rel if rel in set(paths) else None
        # 别的写法（相对路径、另一台电脑上的绝对路径、只有文件名）：按结尾对上的路径段数挑最像的
        parts = [p for p in raw.split("/") if p and p != "."]
        best, best_score = None, 0
        for p in paths:
            mine = p.split("/")
            score = 0
            while score < min(len(mine), len(parts)) and mine[-1 - score].lower() == parts[-1 - score].lower():
                score += 1
            if score > best_score:
                best, best_score = p, score
        return best if best_score else None

    # ---------- 星图 ----------

    def graph(self, modified: set[str] | None = None,
              churn: dict[str, int] | None = None) -> dict[str, list[dict[str, object]]]:
        """文件是星球，文件之间的调用汇总成光线（按名字解析，和 callees 同样保守：同名的有好几个就不连）

        每个文件带上所在的目录（module，星图拉远时按它聚成一个模块）、是不是测试文件（test，星图里调暗）、
        最近的提交里改过几次（churn，「按改动」上色）。
        光线分三种（kind）：call 函数调用；http 请求了另一个文件提供的接口；ipc 发了另一个文件处理的 IPC 消息。
        http、ipc 的 via 是经过的接口路径或频道名（最多 6 个）
        """
        modified = modified or set()
        with self._lock:
            files = self._db.execute("select path, lang, lines, mtime, size from files").fetchall()
            chunks = self._db.execute("select id, file, kind, name from chunks").fetchall()
            calls = self._db.execute("select chunk_id, callee from calls").fetchall()
        file_of = {cid: f for cid, f, _, _ in chunks}
        defined: dict[str, set[str]] = {}
        counts: dict[str, int] = {}
        for _, f, kind, name in chunks:
            if kind in ("function", "method", "class") or kind in ("module", "template") and name[:1].isupper():
                defined.setdefault(name, set()).add(f)  # 大写开头的模块名是 Vue 组件（模板里用到的组件名）
            if kind in ("function", "method"):
                counts[f] = counts.get(f, 0) + 1

        weights: dict[tuple[str, str], int] = {}
        for cid, callee in calls:
            src = file_of.get(cid)
            name = callee.lstrip(".")
            if not src or name not in defined:
                continue
            targets = {t for t in defined[name] if family(t) == family(src)}
            if not targets or src in targets:
                continue  # 调用的是自己文件里的，或者项目里（同一类语言）没有
            if callee.startswith(".") and (name in COMMON_METHODS or len(targets) > 1):
                continue
            if len(targets) > 1:
                folder = src.rsplit("/", 1)[0]
                near = [t for t in targets if t.rsplit("/", 1)[0] == folder]
                if len(near) != 1:
                    continue  # 同名的有好几个，又不在同一个目录：连不准就不连
                targets = set(near)
            dst = next(iter(targets))
            weights[(src, dst)] = weights.get((src, dst), 0) + 1

        tops = {f.split("/", 1)[0] for f, *_ in files if "/" in f}
        def group(path: str) -> str:
            parts = path.split("/")
            if len(tops) > 1:
                return parts[0] if len(parts) > 1 else "（根目录）"
            return parts[1] if len(parts) > 2 else (parts[0] if len(parts) > 1 else "（根目录）")

        nodes = [
            {"id": f, "name": f.rsplit("/", 1)[-1], "group": group(f), "module": f.rsplit("/", 1)[0] if "/" in f else ".",
             "test": is_test(f), "lang": lang, "lines": lines, "functions": counts.get(f, 0), "modified": f in modified,
             "churn": (churn or {}).get(f, 0)}
            for f, lang, lines, _, _ in files
        ]
        links: list[dict[str, object]] = [
            {"source": s, "target": t, "weight": w, "kind": "call"} for (s, t), w in weights.items()
        ]
        # 跨进程的光线：HTTP 接口、IPC 消息（按函数名连不上的那些）
        scanned = {f: self._scan_of(f, lang, mtime, size) for f, lang, _, mtime, size in files}
        found = {f: eps for f, (eps, _) in scanned.items()}
        for (s, t, kind), via in sorted(link(found).items()):
            links.append({"source": s, "target": t, "weight": len(via), "kind": kind, "via": sorted(via)[:6]})
        # import：项目里文件之间的连成光线；项目外的包算第三方库（星图外圈的恒星）
        imports, libs = resolve_imports({f: imp for f, (_, imp) in scanned.items()}, {f: lang for f, lang, *_ in files})
        for (s, t), w in sorted(imports.items()):
            links.append({"source": s, "target": t, "weight": w, "kind": "import"})
        lib_list = sorted(
            ({"id": key, "name": v["name"], "ecosystem": v["ecosystem"], "users": sorted(v["users"])[:200], "count": len(v["users"])}
             for key, v in libs.items()),
            key=lambda x: (-x["count"], x["name"]))[:MAX_LIBS]
        return {"nodes": nodes, "links": links, "libs": lib_list}

    def endpoint_links(self) -> list[tuple[str, Endpoint, str, Endpoint]]:
        """只取 HTTP、IPC 关系，复用星图的静态识别和文件扫描缓存。"""
        with self._lock:
            files = self._db.execute("select path, lang, mtime, size from files order by path").fetchall()
        return connections({f: self._scan_of(f, lang, mtime, size)[0] for f, lang, mtime, size in files})

    def _source_path(self, rel: str) -> Path | None:
        try:
            path = self._workspace.resolve(rel)
        except (WorkspaceError, OSError, RuntimeError):
            return None
        if self._workspace.is_secret(path) or self._workspace.is_secret(self.root / rel):
            return None
        return path

    def _scan_of(self, rel: str, lang: str, mtime: float, size: int) -> tuple[list[Endpoint], list[str]]:
        """一个文件提供、请求的接口，和它 import 了什么：按修改时间和大小缓存，文件没变就不再读"""
        cached = self._endpoints.get(rel)
        if cached and cached[0] == mtime and cached[1] == size:
            return cached[2], cached[3]
        path = self._source_path(rel)
        if path is None:
            return [], []
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return [], []
        found, imported = scan(rel, text, lang), scan_imports(text, lang)
        self._endpoints[rel] = (mtime, size, found, imported)
        return found, imported

    def outline(self, file: str, *, include_module: bool = False) -> list[CodeChunk]:
        """一个文件里的函数和类（按出现的顺序），星图的全息面板用"""
        with self._lock:
            rows = self._db.execute(
                f"select {COLUMNS} from chunks where file = ? and (? or kind != 'module') order by start_line",
                (file, include_module),
            ).fetchall()
        return [CodeChunk(*r) for r in rows]

    def chain(self, chunk: CodeChunk, depth: int = 3, width: int = 3) -> list[CodeChunk]:
        """沿调用图往上找：谁调用了它，又是谁调用了那些（报错时星图沿这条链高亮）"""
        out: list[CodeChunk] = []
        seen = {chunk.id}
        level = [chunk]
        for _ in range(depth):
            nxt = []
            for c in level:
                for caller in self.callers(c, limit=width):
                    if caller.id not in seen:
                        seen.add(caller.id)
                        nxt.append(caller)
            out += nxt
            level = nxt
        return out

    def references(self, identifier: str, *, tests: bool | None = None, limit: int = 6) -> list[tuple[CodeChunk, int]]:
        """全文定位确切拼写，包含报告被截短时还没进函数块的常量和属性。"""
        if not re.fullmatch(r"[A-Za-z_]\w{2,79}", identifier):
            return []
        with self._lock:
            paths = [r[0] for r in self._db.execute(
                "select path from files_fts where files_fts match ? order by path", (f'"{identifier}"',)
            )]
        name = re.compile(rf"(?<![\w$]){re.escape(identifier)}(?![\w$])")
        references: list[tuple[CodeChunk, int]] = []
        for path in paths:
            if tests is not None and is_test(path) != tests:
                continue
            seen: set[int] = set()
            for line, text in enumerate(self.indexed_lines(path), 1):
                if not name.search(text):
                    continue
                chunk = self.chunk_at(path, line)
                if chunk is None or chunk.id in seen:
                    continue
                references.append((chunk, line))
                seen.add(chunk.id)
                if len(references) >= limit:
                    return references
        return references

    def indexed_lines(self, file: str) -> list[str]:
        """代码依据用原始行。只有磁盘内容还和本次索引对得上才返回。"""
        with self._lock:
            row = self._db.execute("select sha1 from files where path = ?", (file,)).fetchone()
        path = self._source_path(file)
        if not row or path is None:
            return []
        try:
            with path.open("rb") as stream:
                data = stream.read(MAX_FILE_BYTES + 1)
        except OSError:
            return []
        if hashlib.sha1(data).hexdigest() != row[0]:
            return []
        return data.decode("utf-8", errors="replace").replace("\r\n", "\n").split("\n")

    def source(self, file: str, start: int, end: int) -> list[str]:
        """从磁盘读这几行（现在的样子，用户刚改过也是新的）"""
        try:
            lines = (self.root / file).read_text(encoding="utf-8", errors="replace").split("\n")
        except OSError:
            return []
        return lines[max(0, start - 1):end]

    def stats(self) -> dict[str, object]:
        with self._lock:
            files, chunks = self._db.execute("select (select count(*) from files), (select count(*) from chunks)").fetchone()
            langs = dict(self._db.execute("select lang, count(*) from files group by lang").fetchall())
            row = self._db.execute("select value from meta where key = 'refreshed_at'").fetchone()
        return {"files": files, "chunks": chunks, "languages": langs, "refreshedAt": float(row[0]) if row else None}

    def close(self) -> None:
        self._db.close()

    # ---------- 内部 ----------

    def _chunks_by_id(self, ids: list[int]) -> list[CodeChunk]:
        if not ids:
            return []
        with self._lock:
            rows = self._db.execute(f"select {COLUMNS} from chunks where id in ({','.join('?' * len(ids))})", ids).fetchall()
        return [CodeChunk(*r) for r in rows]

    def _all_paths(self) -> list[str]:
        with self._lock:
            if self._paths is None:
                self._paths = [r[0] for r in self._db.execute("select path from files")]
            return self._paths

    def _load_vectors(self) -> tuple[np.ndarray, list[int]]:
        with self._lock:
            if self._vectors is None:
                rows = self._db.execute("select id, embedding from chunks where embedding is not null order by id").fetchall()
                ids = [r[0] for r in rows]
                matrix = np.stack([np.frombuffer(r[1], dtype=np.float32) for r in rows]) if rows else np.zeros((0, 512), np.float32)
                self._vectors = (matrix, ids)
            return self._vectors
