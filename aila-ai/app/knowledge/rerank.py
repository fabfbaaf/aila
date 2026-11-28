"""可选的本地重排：候选已按项目过滤，只用已安装的 ONNX 模型。"""

import logging
import math
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

MODEL_NAME = "BAAI/bge-reranker-v2-m3-int8"
MODEL_FILE = "onnx/model_int8.onnx"
Score = Callable[[str, list[str]], list[float]]
log = logging.getLogger("aila")


@lru_cache(maxsize=2)
def local_reranker(model_dir: str) -> Score | None:
    if not model_dir:
        return None
    path = Path(model_dir)
    if not (path / MODEL_FILE).is_file():
        log.warning("本地重排模型文件缺失")
        return None
    try:
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        model = TextCrossEncoder(
            MODEL_NAME, specific_model_path=str(path), local_files_only=True, threads=2,
        )
    except Exception as err:
        log.warning("本地重排模型加载失败：%s", type(err).__name__)
        return None

    def score(query: str, candidates: list[str]) -> list[float]:
        return list(model.rerank(query[:512], [text[:1100] for text in candidates], batch_size=4))

    return score


def rank(score: Score | None, query: str, candidates: list[str]) -> list[int] | None:
    if score is None or len(candidates) < 2:
        return None
    try:
        scores = score(query, candidates)
        if len(scores) == len(candidates) and all(math.isfinite(value) for value in scores):
            return sorted(range(len(scores)), key=lambda i: -scores[i])
    except Exception as err:
        log.info("本地重排未完成：%s", type(err).__name__)
    return None
