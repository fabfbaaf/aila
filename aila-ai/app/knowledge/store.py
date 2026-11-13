"""个人知识库：上传的文件切块后存进本机的 SQLite，检索时关键词和向量两路一起找。

为什么两路：
- 向量检索懂意思（问「怎么登录」能找到「账号验证流程」），但对专有名词、编号、代码不敏感
- 关键词检索（FTS5 + trigram 分词，中文不用分词也能按子串匹配）对「错误码 E1024」这种很准
两路各取前 20，用 RRF（倒数排名融合）合并：不比较两边的分数（尺度不同），只看各自排第几。

文件只存在这台电脑上（aila-ai/data/），不上传云端。
"""

import hashlib
import re
import sqlite3
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from ..memory.embedder import Embedder
from .chunk import chunk
from .parse import parse

CANDIDATES = 20
RRF_K = 60
# 向量最相似的一块都低于这个、关键词也没命中：资料里大概没有，返回空，让她直接说没找到
MIN_VECTOR = 0.35
MAX_TRIGRAMS = 40

SCHEMA = """
create table if not exists documents (
    id       text primary key,
    name     text not null,
    sha256   text not null unique,
    size     integer not null,
    chars    integer not null,
    chunks   integer not null,
    added_at text not null
);
create table if not exists chunks (
    id        integer primary key,
    doc_id    text not null references documents (id) on delete cascade,
    ord       integer not null,
    text      text not null,
    heading   text not null,
    page      integer,
    embedding blob
);
create index if not exists chunks_doc on chunks (doc_id);
create virtual table if not exists chunks_fts using fts5 (body, tokenize = 'trigram');
"""


@dataclass
class Document:
    id: str
    name: str
    size: int
    chars: int
    chunks: int
    addedAt: str


@dataclass
class Hit:
    document: str
    heading: str
    page: int | None
    text: str
    score: float

    @property
    def source(self) -> str:
        """出处：《文件名》第 3 页 · 第三章 > 登录"""
        parts = [f"《{self.document}》"]
        if self.page:
            parts.append(f"第 {self.page} 页")
        if self.heading:
            parts.append(self.heading)
        return " · ".join(parts)


class KnowledgeBase:
    def __init__(self, path: Path | str, embedder: Callable[[], Embedder | None]) -> None:
        """embedder 是个函数：向量模型在后台加载，用到时才去取（没加载成功就只用关键词检索）"""
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("pragma foreign_keys = on")
        self._db.executescript(SCHEMA)
        self._get_embedder = embedder
        self._lock = threading.Lock()
        self._vectors: tuple[np.ndarray, list[int]] | None = None

    @property
    def _embedder(self) -> Embedder | None:
        return self._get_embedder()

    @property
    def empty(self) -> bool:
        with self._lock:
            return self._db.execute("select 1 from documents limit 1").fetchone() is None

    def documents(self) -> list[Document]:
        with self._lock:
            rows = self._db.execute("select id, name, size, chars, chunks, added_at from documents order by added_at desc").fetchall()
        return [Document(*r) for r in rows]

    def add(self, name: str, data: bytes) -> Document:
        """解析、切块、算向量、入库。同一个文件（内容一样）重复上传直接返回已有的"""
        digest = hashlib.sha256(data).hexdigest()
        with self._lock:
            row = self._db.execute("select id, name, size, chars, chunks, added_at from documents where sha256 = ?", (digest,)).fetchone()
        if row:
            return Document(*row)

        chunks = chunk(parse(name, data))
        texts = [c.indexed_text for c in chunks]
        embedder = self._embedder
        vectors = embedder.embed(texts) if embedder else None  # 最慢的一步，不占着锁
        doc = Document(uuid.uuid4().hex, name, len(data), sum(len(c.text) for c in chunks), len(chunks),
                       datetime.now(timezone.utc).isoformat())
        with self._lock, self._db:
            self._db.execute("insert into documents values (?, ?, ?, ?, ?, ?, ?)",
                             (doc.id, doc.name, digest, doc.size, doc.chars, doc.chunks, doc.addedAt))
            for i, c in enumerate(chunks):
                blob = vectors[i].astype(np.float32).tobytes() if vectors is not None else None
                cur = self._db.execute("insert into chunks (doc_id, ord, text, heading, page, embedding) values (?, ?, ?, ?, ?, ?)",
                                       (doc.id, i, c.text, c.heading, c.page, blob))
                self._db.execute("insert into chunks_fts (rowid, body) values (?, ?)", (cur.lastrowid, texts[i]))
            self._vectors = None
        return doc

    def delete(self, doc_id: str) -> bool:
        with self._lock, self._db:
            ids = [r[0] for r in self._db.execute("select id from chunks where doc_id = ?", (doc_id,))]
            self._db.executemany("delete from chunks_fts where rowid = ?", [(i,) for i in ids])
            deleted = self._db.execute("delete from documents where id = ?", (doc_id,)).rowcount > 0
            self._vectors = None
        return deleted

    def search(self, query: str, k: int = 5) -> list[Hit]:
        query = query.strip()
        if not query:
            return []
        ranks: dict[int, float] = {}
        best_vector = 0.0

        matrix, ids = self._load_vectors()
        embedder = self._embedder
        if embedder is not None and len(ids):
            scores = matrix @ embedder.embed_query(query)
            order = np.argsort(-scores)[:CANDIDATES]
            best_vector = float(scores[order[0]])
            for rank, i in enumerate(order):
                ranks[ids[i]] = ranks.get(ids[i], 0.0) + 1 / (RRF_K + rank + 1)

        fts = _fts_query(query)
        keyword_hits = 0
        if fts:
            with self._lock:
                rows = self._db.execute(
                    "select rowid from chunks_fts where chunks_fts match ? order by bm25(chunks_fts) limit ?", (fts, CANDIDATES)
                ).fetchall()
            keyword_hits = len(rows)
            for rank, (cid,) in enumerate(rows):
                ranks[cid] = ranks.get(cid, 0.0) + 1 / (RRF_K + rank + 1)

        if not ranks or (keyword_hits == 0 and best_vector < MIN_VECTOR):
            return []
        top = sorted(ranks.items(), key=lambda kv: -kv[1])[:k]
        with self._lock:
            rows = {
                r[0]: r[1:]
                for r in self._db.execute(
                    f"select c.id, d.name, c.heading, c.page, c.text from chunks c join documents d on d.id = c.doc_id "
                    f"where c.id in ({','.join('?' * len(top))})",
                    [cid for cid, _ in top],
                )
            }
        return [Hit(*rows[cid], score=round(score, 4)) for cid, score in top if cid in rows]

    def close(self) -> None:
        self._db.close()

    def _load_vectors(self) -> tuple[np.ndarray, list[int]]:
        """所有块的向量放进内存，检索就是一次矩阵乘法。个人资料几万块以内都很快"""
        with self._lock:
            if self._vectors is None:
                rows = self._db.execute("select id, embedding from chunks where embedding is not null order by id").fetchall()
                ids = [r[0] for r in rows]
                matrix = np.stack([np.frombuffer(r[1], dtype=np.float32) for r in rows]) if rows else np.zeros((0, 512), np.float32)
                self._vectors = (matrix, ids)
            return self._vectors


def _fts_query(query: str) -> str:
    """trigram 分词按三个字的片段匹配：把查询拆成重叠的三字片段，用 OR 连起来，命中越多排得越前。
    英文单词、数字整个作为一个片段。"""
    grams: list[str] = []
    for run in re.findall(r"[A-Za-z0-9_.\-]{3,}|[一-鿿]+", query):
        if run.isascii():
            grams.append(run)
        else:
            grams.extend(run[i : i + 3] for i in range(len(run) - 2))
    unique = list(dict.fromkeys(grams))[:MAX_TRIGRAMS]
    return " OR ".join('"' + g.replace('"', '""') + '"' for g in unique)
