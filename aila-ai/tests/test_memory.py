"""长期记忆：回想挑哪些、提取出的操作怎么校验、接口"""

import json
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.llm import MockModel, ToolCall
from app.main import app, get_embedder, get_model
from app.memory import Memory, Turn, describe, extract, recall
from app.memory.extract import heuristic
from app.persona import SYSTEM_PROMPT

NOW = datetime(2026, 11, 12, 10, 0, tzinfo=timezone(timedelta(hours=8)))


class FakeEmbedder:
    """按字的二元组做向量：字面越像越相似，足够测挑选逻辑"""

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(512, dtype=np.float32)
        for a, b in zip(text, text[1:]):
            v[hash(a + b) % 512] += 1
        n = np.linalg.norm(v)
        return v / n if n else v

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.stack([self._vec(t) for t in texts]) if texts else np.zeros((0, 512), np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return self._vec(text)


def mem(i: int, content: str, kind: str = "other", expires: datetime | None = None, age_days: int = 0) -> Memory:
    return Memory(f"id{i}", content, kind, expires, NOW - timedelta(days=age_days))


# ---------- 回想 ----------


def test_few_memories_are_all_used_except_expired() -> None:
    memories = [mem(1, "用户叫小王"), mem(2, "用户上周三有一场面试", "event", NOW - timedelta(days=1))]
    assert [m.id for m in recall(memories, "你好", None, NOW)] == ["id1"]


def test_profile_and_upcoming_events_are_always_included() -> None:
    memories = [
        mem(1, "用户是一名后端实习生", "profile", age_days=1),
        mem(2, "用户明天下午有一场面试", "event", NOW + timedelta(days=1)),
        mem(3, "用户下个月要去旅行", "event", NOW + timedelta(days=30)),
        *[mem(10 + i, f"用户喜欢第{i}种水果", "preference") for i in range(10)],
    ]
    picked = {m.id for m in recall(memories, "今天天气怎么样", FakeEmbedder(), NOW)}
    assert {"id1", "id2"} <= picked
    assert "id3" not in picked  # 还远，和话题也无关
    assert len(picked) <= 8


def test_relevant_memories_are_chosen_and_irrelevant_ones_are_not() -> None:
    memories = [mem(i, f"用户收藏了第{i}张唱片") for i in range(10)]
    memories.append(mem(99, "用户养了一只叫团子的橘猫"))
    # 假向量只看字面：猫那条和查询重合的二字组多（约 0.57），唱片那几条一个都不重合（0），一条都不该带上
    picked = [m.id for m in recall(memories, "我养的那只叫团子的橘猫", FakeEmbedder(), NOW)]
    assert picked == ["id99"]


def test_without_embedder_falls_back_to_recent_ones() -> None:
    memories = [mem(i, f"用户的第{i}件事", age_days=i) for i in range(20)]
    picked = [m.id for m in recall(memories, "随便", None, NOW)]
    assert picked == [f"id{i}" for i in range(8)]


def test_describe_has_date_and_memories() -> None:
    text = describe([mem(1, "用户明天有面试", "event", NOW + timedelta(days=1))], NOW)
    assert "2026 年 11 月 12 日，星期四" in text
    assert "- 用户明天有面试（到 11 月 13 日）" in text
    assert "根据我的记忆" in text  # 提醒她别这样说
    assert "记得" not in describe([], NOW)


# ---------- 提取 ----------


class JsonModel:
    """假装是大模型：complete() 返回给定的 JSON，并记下收到的提示词"""

    def __init__(self, ops: list[dict[str, Any]] | str) -> None:
        self.reply = ops if isinstance(ops, str) else json.dumps({"ops": ops}, ensure_ascii=False)
        self.prompt = ""

    async def complete(self, messages: list[dict[str, Any]]) -> str:
        self.prompt = messages[-1]["content"]
        return self.reply


@pytest.mark.anyio
async def test_extract_maps_short_ids_back_and_validates() -> None:
    memories = [mem(1, "用户住在北京", "profile"), mem(2, "用户喜欢喝美式", "preference")]
    model = JsonModel([
        {"op": "update", "id": "m1", "content": "用户搬到了上海"},
        {"op": "delete", "id": "m2"},
        {"op": "delete", "id": "m9"},  # 没有这个编号
        {"op": "add", "content": "用户下周三有一场面试", "kind": "event", "expires": "2026-11-18"},
        {"op": "add", "content": "用户的银行卡密码是 123456", "kind": "other"},  # 敏感
        {"op": "add", "content": "用户手机号 13800138000", "kind": "other"},  # 敏感
        {"op": "add", "content": "用户住在北京", "kind": "profile"},  # 已经有了
        {"op": "add", "content": "短", "kind": "other"},  # 太短
        {"op": "add", "content": "用户在杭州读过大学", "kind": "weird"},  # kind 不认识 → other
    ])
    ops = await extract(model, [Turn("user", "我搬到上海了，不喝咖啡了。下周三有面试")], memories, None, NOW)

    assert ops == [
        {"op": "update", "id": "id1", "content": "用户搬到了上海", "kind": "profile", "expiresAt": None},
        {"op": "delete", "id": "id2"},
        {"op": "add", "content": "用户下周三有一场面试", "kind": "event", "expiresAt": "2026-11-18T23:59:59+08:00"},
        {"op": "add", "content": "用户在杭州读过大学", "kind": "other", "expiresAt": None},
    ]
    assert "m1: 用户住在北京" in model.prompt
    assert "今天是 2026-11-12（星期四）" in model.prompt


@pytest.mark.anyio
async def test_extract_ignores_past_dates_and_garbage() -> None:
    model = JsonModel([{"op": "add", "content": "用户上周有一场面试", "kind": "event", "expires": "2026-11-01"}])
    ops = await extract(model, [Turn("user", "上周面试了")], [], None, NOW)
    assert ops == [{"op": "add", "content": "用户上周有一场面试", "kind": "event", "expiresAt": None}]

    assert await extract(JsonModel("我不知道"), [Turn("user", "hi")], [], None, NOW) == []
    assert await extract(JsonModel('{"ops": "nope"}'), [Turn("user", "hi")], [], None, NOW) == []


@pytest.mark.anyio
async def test_near_duplicate_is_not_added() -> None:
    model = JsonModel([{"op": "add", "content": "用户养了一只叫团子的橘猫！", "kind": "profile"}])
    ops = await extract(model, [Turn("user", "我的猫叫团子")], [mem(1, "用户养了一只叫团子的橘猫")], FakeEmbedder(), NOW)
    assert ops == []


def test_heuristic_without_llm() -> None:
    numbered = {"m1": mem(1, "用户喜欢喝美式", "preference")}
    assert heuristic([Turn("user", "你好，我叫小王。记住，我喜欢爬山")], {}) == [
        {"op": "add", "content": "用户叫小王", "kind": "profile", "expires": None},
        {"op": "add", "content": "用户喜欢爬山", "kind": "preference", "expires": None},
    ]
    assert heuristic([Turn("user", "忘掉我喜欢喝美式这件事吧")], numbered) == [{"op": "delete", "id": "m1"}]
    assert heuristic([Turn("assistant", "我是艾拉")], {}) == []


# ---------- 接口 ----------


class RecordingModel:
    name = "recording"

    def __init__(self) -> None:
        self.system = ""
        self.last = ""

    async def stream(self, messages: list[dict[str, Any]], tools: Any = None) -> AsyncIterator[str | ToolCall]:
        self.system = messages[0]["content"]
        self.last = messages[-1]["content"]
        yield "好的"


def test_chat_puts_relevant_memories_before_the_latest_message() -> None:
    model = RecordingModel()
    app.dependency_overrides[get_model] = lambda: model
    app.dependency_overrides[get_embedder] = FakeEmbedder
    try:
        memories = [{"id": f"x{i}", "content": f"用户收藏了第{i}张唱片"} for i in range(12)]
        memories.append({"id": "cat", "content": "用户养了一只叫团子的橘猫", "kind": "profile"})
        memories.append({"id": "old", "content": "用户昨天有面试", "kind": "event", "expiresAt": "2020-01-01T00:00:00Z"})
        res = TestClient(app).post("/chat", json={"messages": [{"role": "user", "content": "团子今天好乖"}], "memories": memories})
    finally:
        app.dependency_overrides.clear()
    assert res.status_code == 200
    assert model.system == SYSTEM_PROMPT  # 系统提示词一个字不变，前缀缓存才能命中
    assert model.last.startswith("<背景>\n今天是")
    assert "- 用户养了一只叫团子的橘猫" in model.last
    assert "昨天有面试" not in model.last
    assert model.last.endswith("</背景>\n\n团子今天好乖")


def test_memory_extract_endpoint_with_mock_model() -> None:
    app.dependency_overrides[get_model] = lambda: MockModel(delay=0)
    try:
        res = TestClient(app).post("/memory/extract", json={
            "messages": [{"role": "user", "content": "记住，我喜欢喝美式"}, {"role": "assistant", "content": "好的。"}],
            "memories": [{"id": "a", "content": "用户叫小王", "kind": "profile"}],
        })
    finally:
        app.dependency_overrides.clear()
    assert res.json() == {"ops": [{"op": "add", "content": "用户喜欢喝美式", "kind": "preference", "expiresAt": None}]}
