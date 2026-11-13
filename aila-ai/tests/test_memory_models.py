"""用真实的向量模型（bge-small-zh）检查回想和去重的阈值。没下载模型时跳过。"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.memory import Memory, recall
from app.memory.extract import DUPLICATE

MODELS = Path(__file__).resolve().parent.parent / "models"
pytestmark = pytest.mark.skipif(not (MODELS / "embed").exists(), reason="没有下载向量模型（scripts/download_models.py embed）")

NOW = datetime(2026, 11, 12, 10, 0, tzinfo=timezone(timedelta(hours=8)))


@pytest.fixture(scope="module")
def embedder():  # type: ignore[no-untyped-def]
    from app.memory import FastEmbedder

    return FastEmbedder(MODELS)


MEMORIES = [
    Memory("job", "用户是一名后端实习生，正在准备跳槽"),
    Memory("coffee", "用户喜欢喝美式咖啡，不加糖", "preference"),
    Memory("cat", "用户养了一只叫团子的橘猫"),
    Memory("learn", "用户在学习 Spring Boot 和 Vue 3"),
    Memory("city", "用户住在杭州"),
    Memory("music", "用户喜欢听坂本龙一的钢琴曲", "preference"),
    Memory("run", "用户每周跑步三次", "preference"),
    Memory("sister", "用户有一个在读高中的妹妹"),
    Memory("game", "用户周末喜欢玩塞尔达", "preference"),
    Memory("food", "用户不吃香菜", "preference"),
]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("我家猫今天又捣乱了", "cat"),
        ("推荐一杯喝的", "coffee"),
        ("中午吃什么好", "food"),
        ("Java 后端框架学到哪了", "learn"),
    ],
)
def test_recall_finds_the_relevant_memory(embedder, query: str, expected: str) -> None:  # type: ignore[no-untyped-def]
    picked = [m.id for m in recall(MEMORIES, query, embedder, NOW)]
    assert expected in picked
    assert len(picked) < len(MEMORIES)  # 不相关的没有全塞进去


def test_knowledge_search_understands_meaning(embedder, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """资料库：问法和原文一个字都不重合时，靠向量也能找到"""
    from app.knowledge import KnowledgeBase

    from .test_knowledge import MANUAL

    kb = KnowledgeBase(tmp_path / "kb.sqlite3", lambda: embedder)
    kb.add("手册.md", MANUAL.encode())
    hits = kb.search("怎么叫她起来")
    assert hits and "你好艾拉" in hits[0].text


def test_rephrasing_is_a_duplicate_but_a_new_fact_is_not(embedder) -> None:  # type: ignore[no-untyped-def]
    base = embedder.embed(["用户养了一只叫团子的橘猫"])[0]
    same = embedder.embed_query("用户养了一只橘猫，名字叫团子")
    other = embedder.embed_query("用户养了一只叫小白的柯基")
    assert float(base @ same) >= DUPLICATE - 0.05
    assert float(base @ other) < DUPLICATE
