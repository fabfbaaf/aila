"""她的性格：认识多久决定拘谨还是放松；喜好每次都带上；记忆库里的记忆能取回；手机上聊天也带着记忆。"""

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import deps
from app.main import app
from app.memory.recall import ALWAYS_PREFERENCE, Memory, recall
from app.memory.store import MemoryStore
from app.persona import SYSTEM_PROMPT, bond_note
from app.routes.chat import Bond, ChatMessage, ChatRequest, memory_context

NOW = datetime(2026, 11, 26, 2, 0).astimezone()


def test_bond_stages() -> None:
    assert "刚认识" in bond_note(0, 0)
    assert "第 1 天" in bond_note(0, 0)
    assert "熟一些" in bond_note(5, 40)
    assert "很熟" in bond_note(30, 500)
    # 天数和句数都要到：装上一个月不怎么聊，还是刚认识
    assert "刚认识" in bond_note(30, 5)
    assert "刚认识" in bond_note(1, 900)


def test_persona_has_her_temperament() -> None:
    for words in ("笨拙", "孩子气", "不好意思", "认识多久"):
        assert words in SYSTEM_PROMPT


def test_memory_context_puts_bond_after_date() -> None:
    req = ChatRequest(messages=[ChatMessage(role="user", content="你好")], bond=Bond(days=20, talks=300))
    text = asyncio.run(memory_context(req, None))
    lines = text.splitlines()
    assert lines[0].startswith("今天是")
    assert lines[1].startswith("你们认识第 21 天") and "很熟" in lines[1]
    plain = asyncio.run(memory_context(ChatRequest(messages=[ChatMessage(role="user", content="你好")]), None))
    assert "认识" not in plain


def test_recall_always_brings_preferences() -> None:
    other = [Memory(f"o{i}", f"用户的第 {i} 件小事", "other", updated_at=NOW - timedelta(hours=i)) for i in range(20)]
    prefs = [Memory(f"p{i}", f"用户喜欢第 {i} 样东西", "preference", updated_at=NOW - timedelta(days=3 + i)) for i in range(5)]
    picked = recall(other + prefs, "今天写点什么", None, NOW)
    got = [m.id for m in picked if m.kind == "preference"]
    assert got == [f"p{i}" for i in range(ALWAYS_PREFERENCE)]


@pytest.fixture
def store(tmp_path: Path):
    s = MemoryStore(tmp_path / "m.sqlite3", lambda: None)
    s.sync_facts([Memory("f1", "用户喜欢看美食类视频", "preference", None, NOW)])
    yield s
    s.close()


def test_facts_route_returns_memory_library(store: MemoryStore) -> None:
    app.dependency_overrides[deps.get_memory_store] = lambda: store
    try:
        body = TestClient(app).get("/memory/facts").json()
    finally:
        app.dependency_overrides.pop(deps.get_memory_store, None)
    assert body["memories"] == [
        {"id": "f1", "content": "用户喜欢看美食类视频", "kind": "preference", "expiresAt": None, "updatedAt": body["memories"][0]["updatedAt"]}
    ]


def test_phone_chat_uses_memory_library(store: MemoryStore, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.phone import gateway
    from app.routes import chat as chat_route

    seen: dict[str, Any] = {}

    async def fake_chat(req: ChatRequest, **kw: Any) -> str:
        seen["req"] = req
        return "ok"

    monkeypatch.setattr(chat_route, "chat", fake_chat)
    monkeypatch.setattr(deps, "get_memory_store", lambda: store)
    monkeypatch.setattr(deps, "get_embedder", lambda: None)
    monkeypatch.setattr(deps, "get_model", lambda: None)
    monkeypatch.setattr(deps, "get_registry", lambda: None)
    monkeypatch.setattr(deps, "get_confirmations", lambda: None)
    monkeypatch.setattr(deps, "get_phone", lambda: SimpleNamespace(skills={}))
    asyncio.run(gateway.default_chat([{"role": "user", "content": "找点吃的视频"}]))
    assert [m.content for m in seen["req"].memories] == ["用户喜欢看美食类视频"]
    # 记忆库没有被对齐成空的
    assert [m.id for m in store.facts()] == ["f1"]
