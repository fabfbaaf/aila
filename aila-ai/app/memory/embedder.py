"""文本向量：本地跑 bge-small-zh（约 90MB，CPU 上一句几毫秒），记忆和知识库共用"""

from collections import OrderedDict
from pathlib import Path
from threading import Lock
from typing import Protocol

import numpy as np

MODEL_NAME = "BAAI/bge-small-zh-v1.5"
CACHE_SIZE = 20_000


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> np.ndarray:
        """每行一个单位向量（点积就是余弦相似度）"""
        ...

    def embed_query(self, text: str) -> np.ndarray: ...


class FastEmbedder:
    """同一段文字只算一次：记忆每次对话都要拿来比较，缓存后基本只算新增的"""

    def __init__(self, models_dir: Path) -> None:
        from fastembed import TextEmbedding  # 导入要一两秒，用到时才导入

        self._model = TextEmbedding(MODEL_NAME, cache_dir=str(models_dir / "embed"))
        self._cache: OrderedDict[str, np.ndarray] = OrderedDict()
        self._lock = Lock()

    def embed(self, texts: list[str]) -> np.ndarray:
        with self._lock:
            missing = list(dict.fromkeys(t for t in texts if t not in self._cache))
        if missing:
            vectors = list(self._model.embed(missing))
            with self._lock:
                for t, v in zip(missing, vectors):
                    self._cache[t] = np.asarray(v, dtype=np.float32)
                while len(self._cache) > CACHE_SIZE:
                    self._cache.popitem(last=False)
        with self._lock:
            return np.stack([self._cache[t] for t in texts]) if texts else np.zeros((0, 512), np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return np.asarray(next(iter(self._model.query_embed(text))), dtype=np.float32)
