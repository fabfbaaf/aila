"""记忆库：她记得的事都放进本机的一个 SQLite，对话和编程任务从同一个地方回想。

放三类：
- fact（关于你的事）：桌面端的长期记忆。桌面端仍然是正本（还要经云端在设备之间同步），
  这里是它的一份副本，多存了向量：以前每次对话都要把全部记忆重新算一遍向量，现在只算新的、改过的
- episode（经历）：对话太长时，滑出窗口的那段的摘要；编程任务每一轮做了什么（改了哪些文件、结果怎样）
- project（项目约定）：编程任务里记下的项目记忆（正本是 runtime/memory.py 的 project_memory.json），
  放进来是为了对话里也能想起「那个项目的测试怎么跑」

检索和资料库（knowledge/store.py）一样，关键词（FTS5 trigram）和向量两路各取前 20，用 RRF 合并：
专有名词、项目名、命令靠关键词找，「上次那个登录的问题」这种靠意思找。
trigram 找不到两个字的词，记忆里偏偏常有（名字、昵称、地名），所以再加一路两字片段（按少见程度加权）。
结果里带上向量相似度和有没有命中关键词，调用的人自己决定要多相关才用（回想事实和回想经历的门槛不一样）。

向量模型在后台加载，没加载好时先存文字、向量留空，以后检索时补上。
数据只在这台电脑上（<数据目录>/memory.sqlite3），不上传。
"""

import hashlib
import json
import math
import re
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from ..knowledge.store import _fts_query
from .embedder import Embedder
from .recall import Memory

CANDIDATES = 20
RRF_K = 60
# 两字片段：最多看这么多个；出现在超过这个比例的条目里的算常用词，不算命中
MAX_GRAMS = 40
COMMON = 0.2
# 每个范围（对话、某个项目）最多留这么多条经历，多了删最早的
MAX_EPISODES = 300
FACT, EPISODE, PROJECT = "fact", "episode", "project"

SCHEMA = """
create table if not exists entries (
    rid        integer primary key,
    id         text not null unique,
    kind       text not null,
    scope      text not null default '',
    content    text not null,
    meta       text not null default '{}',
    created_at real not null,
    updated_at real not null,
    embedding  blob
);
create index if not exists entries_kind on entries (kind, scope, updated_at);
create virtual table if not exists entries_fts using fts5 (body, tokenize = 'trigram');
create table if not exists digested (
    hash text primary key,
    at   real not null
);
"""


def short_terms(query: str) -> list[str]:
    """为中文的短词保留两字匹配，英文按单词匹配。"""
    grams = []
    for run in re.findall(r"[A-Za-z0-9_]{2,}|[一-鿿]{2,}", query):
        grams.extend([run] if run.isascii() else [run[i:i + 2] for i in range(len(run) - 1)])
    return list(dict.fromkeys(g.lower() for g in grams))[:MAX_GRAMS]


@dataclass
class Entry:
    id: str
    kind: str
    scope: str
    content: str
    meta: dict[str, Any] = field(default_factory=dict)
    updated_at: float = 0.0


@dataclass
class Hit:
    entry: Entry
    score: float  # RRF 合并后的分数，只用来排序
    similarity: float | None  # 向量相似度；没有向量模型时是 None
    keyword: bool  # 关键词命中了没有


class MemoryStore:
    def __init__(self, path: Path | str, embedder: Callable[[], Embedder | None]) -> None:
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(SCHEMA)
        self._get_embedder = embedder
        self._lock = threading.Lock()
        self._vectors: tuple[np.ndarray, list[int]] | None = None

    # ---------- 写 ----------

    def sync_facts(self, memories: Iterable[Memory]) -> int:
        """桌面端的全部记忆对齐过来：没了的删、新的和改过的写进去。返回改了几条"""
        wanted = {m.id: m for m in memories}
        with self._lock:
            have = {r[0]: (r[1], r[2]) for r in self._db.execute("select id, content, meta from entries where kind = ?", (FACT,))}
        gone = [i for i in have if i not in wanted]
        changed = [m for m in wanted.values() if have.get(m.id) != (m.content, _fact_meta(m))]
        if not gone and not changed:
            return 0
        vectors = self._embed([m.content for m in changed])
        now = time.time()
        with self._lock, self._db:
            for i in gone:
                self._delete(i)
            for n, m in enumerate(changed):
                updated = m.updated_at.timestamp() if m.updated_at else now
                self._put(Entry(m.id, FACT, "", m.content, json.loads(_fact_meta(m)), updated),
                          vectors[n] if vectors is not None else None)
            self._vectors = None
        return len(gone) + len(changed)

    def facts(self) -> list[Memory]:
        out = []
        for e in self._list("select id, kind, scope, content, meta, updated_at from entries where kind = ? order by updated_at desc", (FACT,)):
            out.append(Memory(e.id, e.content, str(e.meta.get("kind", "other")), _time(e.meta.get("expires")),
                              datetime.fromtimestamp(e.updated_at).astimezone()))
        return out

    def add_episode(self, scope: str, content: str, meta: dict[str, Any] | None = None) -> Entry:
        """记一段经历；同一个范围里超过上限就删最早的"""
        entry = Entry("e-" + uuid.uuid4().hex[:16], EPISODE, scope, content.strip(), dict(meta or {}), time.time())
        vectors = self._embed([entry.content])
        with self._lock, self._db:
            self._put(entry, vectors[0] if vectors is not None else None)
            old = [r[0] for r in self._db.execute(
                "select id from entries where kind = ? and scope = ? order by updated_at desc limit -1 offset ?",
                (EPISODE, scope, MAX_EPISODES))]
            for i in old:
                self._delete(i)
            self._vectors = None
        return entry

    def sync_project(self, scope: str, notes: Iterable[tuple[str, str]]) -> None:
        """一个项目的项目记忆（id，文字）整个对齐过来"""
        wanted = {f"p-{nid}": text for nid, text in notes}
        with self._lock:
            have = {r[0]: r[1] for r in self._db.execute(
                "select id, content from entries where kind = ? and scope = ?", (PROJECT, scope))}
        gone = [i for i in have if i not in wanted]
        new = [(i, t) for i, t in wanted.items() if have.get(i) != t]
        if not gone and not new:
            return
        vectors = self._embed([t for _, t in new])
        now = time.time()
        with self._lock, self._db:
            for i in gone:
                self._delete(i)
            for n, (i, t) in enumerate(new):
                self._put(Entry(i, PROJECT, scope, t, {}, now), vectors[n] if vectors is not None else None)
            self._vectors = None

    def forget_episode(self, entry_id: str) -> bool:
        """删一条经历（事实和项目约定各有正本，不从这里删）"""
        with self._lock, self._db:
            row = self._db.execute("select kind from entries where id = ?", (entry_id,)).fetchone()
            deleted = row is not None and row[0] == EPISODE and self._delete(entry_id)
            self._vectors = None
        return deleted

    def forget_episodes(self) -> int:
        """经历全部删掉。摘要过的指纹留着：删了的对话不会因为还在窗口外又被摘要回来"""
        with self._lock, self._db:
            ids = [r[0] for r in self._db.execute("select id from entries where kind = ?", (EPISODE,))]
            for i in ids:
                self._delete(i)
            self._vectors = None
        return len(ids)

    # ---------- 对话摘要记到哪了 ----------

    def digested(self, hashes: Iterable[str]) -> set[str]:
        hashes = list(hashes)
        if not hashes:
            return set()
        with self._lock:
            rows = self._db.execute(f"select hash from digested where hash in ({','.join('?' * len(hashes))})", hashes)
            return {r[0] for r in rows}

    def mark_digested(self, hashes: Iterable[str]) -> None:
        now = time.time()
        with self._lock, self._db:
            self._db.executemany("insert or ignore into digested values (?, ?)", [(h, now) for h in hashes])

    # ---------- 读 ----------

    def recent(self, kind: str, scope: str | None = None, n: int = 3, source: str | None = None) -> list[Entry]:
        sql = "select id, kind, scope, content, meta, updated_at from entries where kind = ?"
        args: list[Any] = [kind]
        if scope is not None:
            sql += " and scope = ?"
            args.append(scope)
        if source is not None:
            sql += " and json_extract(meta, '$.source') = ?"
            args.append(source)
        sql += " order by updated_at desc, rid desc limit ?"
        args.append(n)
        return self._list(sql, tuple(args))

    def count(self, kind: str | None = None) -> int:
        with self._lock:
            if kind is None:
                return self._db.execute("select count(*) from entries").fetchone()[0]
            return self._db.execute("select count(*) from entries where kind = ?", (kind,)).fetchone()[0]

    def search(self, query: str, kinds: Iterable[str], scope: str | None = None, k: int = 8) -> list[Hit]:
        """关键词和向量两路一起找，按 RRF 排。scope 给了就只在这个范围里找（对话是空字符串，项目是路径）"""
        query = query.strip()
        kinds = tuple(kinds)
        if not query or not kinds:
            return []
        allowed = self._ids(kinds, scope)
        if not allowed:
            return []
        ranks: dict[int, float] = {}
        sims: dict[int, float] = {}
        keyword: set[int] = set()

        embedder = self._embedder()
        if embedder is not None:
            self._backfill(embedder)
            matrix, rids = self._load_vectors()
            mask = np.array([r in allowed for r in rids], dtype=bool)
            if mask.any():
                sub_rids = [r for r, ok in zip(rids, mask) if ok]
                scores = matrix[mask] @ embedder.embed_query(query)
                for rank, i in enumerate(np.argsort(-scores)[:CANDIDATES]):
                    rid = sub_rids[i]
                    sims[rid] = float(scores[i])
                    ranks[rid] = ranks.get(rid, 0.0) + 1 / (RRF_K + rank + 1)

        fts = _fts_query(query)
        if fts:
            with self._lock:
                rows = self._db.execute(
                    "select rowid from entries_fts where entries_fts match ? order by bm25(entries_fts) limit ?",
                    (fts, CANDIDATES * 4)).fetchall()
            hits = [rid for (rid,) in rows if rid in allowed][:CANDIDATES]
            for rank, rid in enumerate(hits):
                keyword.add(rid)
                ranks[rid] = ranks.get(rid, 0.0) + 1 / (RRF_K + rank + 1)
        for rank, rid in enumerate(self._short_words(query, allowed)):
            keyword.add(rid)
            ranks[rid] = ranks.get(rid, 0.0) + 1 / (RRF_K + rank + 1)

        top = sorted(ranks.items(), key=lambda kv: -kv[1])[:k]
        if not top:
            return []
        with self._lock:
            rows = {r[0]: r[1:] for r in self._db.execute(
                f"select rid, id, kind, scope, content, meta, updated_at from entries where rid in ({','.join('?' * len(top))})",
                [rid for rid, _ in top])}
        out = []
        for rid, score in top:
            if rid in rows:
                i, kind, sc, content, meta, updated = rows[rid]
                out.append(Hit(Entry(i, kind, sc, content, _json(meta), updated), round(score, 5),
                               sims.get(rid) if embedder is not None else None, rid in keyword))
        return out

    def close(self) -> None:
        self._db.close()

    # ---------- 内部 ----------

    def _embedder(self) -> Embedder | None:
        try:
            return self._get_embedder()
        except Exception:  # 向量模型加载失败：只用关键词
            return None

    def _embed(self, texts: list[str]) -> np.ndarray | None:
        if not texts:
            return None
        embedder = self._embedder()
        return embedder.embed(texts) if embedder is not None else None

    def _put(self, e: Entry, vector: np.ndarray | None) -> None:
        """调用的人拿着锁、开着事务"""
        self._delete(e.id)
        blob = vector.astype(np.float32).tobytes() if vector is not None else None
        cur = self._db.execute(
            "insert into entries (id, kind, scope, content, meta, created_at, updated_at, embedding) values (?, ?, ?, ?, ?, ?, ?, ?)",
            (e.id, e.kind, e.scope, e.content, json.dumps(e.meta, ensure_ascii=False, sort_keys=True), time.time(), e.updated_at, blob))
        self._db.execute("insert into entries_fts (rowid, body) values (?, ?)", (cur.lastrowid, e.content))

    def _delete(self, entry_id: str) -> bool:
        row = self._db.execute("select rid from entries where id = ?", (entry_id,)).fetchone()
        if row is None:
            return False
        self._db.execute("delete from entries_fts where rowid = ?", (row[0],))
        self._db.execute("delete from entries where rid = ?", (row[0],))
        return True

    def _list(self, sql: str, args: tuple) -> list[Entry]:
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [Entry(r[0], r[1], r[2], r[3], _json(r[4]), r[5]) for r in rows]

    def _short_words(self, query: str, allowed: set[int]) -> list[int]:
        """两个字的词（名字、昵称：「团子」「小王」）trigram 分词找不到：按两字片段直接在原文里找。
        片段越少见分越高；几乎每条都有的（「用户」「今天」）不算，免得什么都沾边"""
        grams = short_terms(query)
        if not grams:
            return []
        total = len(allowed)
        scores: dict[int, float] = {}
        with self._lock:
            for g in grams:
                found = [r[0] for r in self._db.execute("select rid from entries where instr(lower(content), ?) > 0", (g,))
                         if r[0] in allowed]
                if not found or len(found) > max(2, total * COMMON):
                    continue
                weight = math.log(1 + total / len(found))
                for rid in found:
                    scores[rid] = scores.get(rid, 0.0) + weight
        return [rid for rid, _ in sorted(scores.items(), key=lambda kv: -kv[1])[:CANDIDATES]]

    def _ids(self, kinds: tuple[str, ...], scope: str | None) -> set[int]:
        sql = f"select rid from entries where kind in ({','.join('?' * len(kinds))})"
        args: list[Any] = list(kinds)
        if scope is not None:
            sql += " and scope = ?"
            args.append(scope)
        with self._lock:
            return {r[0] for r in self._db.execute(sql, args)}

    def _backfill(self, embedder: Embedder) -> None:
        """向量模型没加载好时存进来的，补上向量"""
        with self._lock:
            rows = self._db.execute("select rid, content from entries where embedding is null limit 500").fetchall()
        if not rows:
            return
        vectors = embedder.embed([r[1] for r in rows])
        with self._lock, self._db:
            self._db.executemany("update entries set embedding = ? where rid = ?",
                                 [(vectors[i].astype(np.float32).tobytes(), r[0]) for i, r in enumerate(rows)])
            self._vectors = None

    def _load_vectors(self) -> tuple[np.ndarray, list[int]]:
        with self._lock:
            if self._vectors is None:
                rows = self._db.execute("select rid, embedding from entries where embedding is not null order by rid").fetchall()
                rids = [r[0] for r in rows]
                matrix = np.stack([np.frombuffer(r[1], dtype=np.float32) for r in rows]) if rows else np.zeros((0, 512), np.float32)
                self._vectors = (matrix, rids)
            return self._vectors


def message_hash(role: str, content: str, before: str = "") -> str:
    """一句话的指纹（带上前一句，免得「好的」「嗯」这种短句撞在一起）"""
    return hashlib.sha1(f"{role}\n{content}\n{before[-200:]}".encode("utf-8")).hexdigest()


def _fact_meta(m: Memory) -> str:
    return json.dumps({"kind": m.kind, "expires": m.expires_at.isoformat() if m.expires_at else None},
                      ensure_ascii=False, sort_keys=True)


def _json(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _time(text: Any) -> datetime | None:
    if not isinstance(text, str):
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None
