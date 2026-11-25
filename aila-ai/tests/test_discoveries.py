"""关注只从用户的话学，官网新内容只入队一次，收到桌面回执才算展示。"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.deps import get_embedder, get_model
from app.discoveries import DAY, INTERVAL, Discoveries, mock_topics, snapshot
from app.main import app
from app.routes.discoveries import get_discoveries
from app.tools.reader import Page

pytestmark = pytest.mark.anyio
NOW = datetime(2026, 11, 25, tzinfo=timezone.utc)


class Model:
    calls = 0
    error = False

    async def complete(self, messages):
        self.calls += 1
        if self.error:
            raise TimeoutError()
        if "长期记忆" in messages[0]["content"]:
            return ('{"ops":[{"op":"add","content":"用户喜欢测试","kind":"preference"}],'
                    '"topics":[{"line":1,"source":"gemini","action":"follow","focus":"releases"},'
                    '{"line":2,"source":"deepseek","action":"follow"}]}')
        assert "新增的模型" in messages[1]["content"]
        return '{"title":"有新的模型更新","summary":"官方文档更新了一项模型能力。"}'


def line(text, n=1, day=NOW):
    return {"line": n, "content": text, "id": f"msg-{n}", "day": day.date().isoformat()}


def page(body):
    return Page("", "官方更新", body, False)


@pytest.fixture
def source(tmp_path: Path):
    state = {"now": NOW, "contents": "目录\n时间: 2026-11-20\n旧模型", "fetches": 0}
    model = Model()

    async def read(url):
        assert url.startswith("https://")
        state["fetches"] += 1
        return page(state["contents"])

    def reopen():
        return Discoveries(tmp_path / "discoveries.sqlite3", read, lambda: model, lambda: state["now"])

    return reopen(), state, model, reopen


def test_snapshots_support_markdown_and_official_changelog_headings():
    assert snapshot(page("Nav ## November 19, 2026 New ## November 18, 2026 Previous ## November 17, 2026 Old")) == (
        "## November 19, 2026 New\n## November 18, 2026 Previous"
    )
    assert snapshot(page("Changelog\nNovember, 2026\nLatest\nOctober, 2026\nPrevious\nJune, 2026\nOld")) == (
        "November, 2026\nLatest\nOctober, 2026\nPrevious"
    )


async def test_chat_extract_uses_one_call_and_ignores_assistant_evidence(source):
    store, _, model, _ = source
    original = dict(app.dependency_overrides)
    app.dependency_overrides.update({
        get_model: lambda: model, get_embedder: lambda: None, get_discoveries: lambda: store
    })
    req = {
        "messages": [
            {"role":"user", "id":"user-a", "createdAt":NOW.isoformat(), "content":"以后 Gemini 出新模型告诉我"},
            {"role":"assistant", "id":"bot-a", "createdAt":NOW.isoformat(), "content":"我还会关注 DeepSeek"}
        ]
    }
    try:
        client = TestClient(app)
        off = client.post("/memory/extract", json=req)
        assert off.status_code == 200 and store.state()["topics"] == []
        before = model.calls
        res = client.post("/memory/extract", json={**req, "learnTopics": True})
        assert model.calls == before + 1
        assert res.json()["ops"][0]["content"] == "用户喜欢测试"
        assert client.get("/discoveries").json()["topics"] == [
            {"source":"gemini","name":"Gemini","state":"active","focus":"releases","slow":False}
        ]
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original)


async def test_two_dates_teach_low_frequency_and_stop_survives_replay(source):
    store, state, _, reopen = source
    one = line("Gemini 的价格是多少")
    two = line("Gemini 可以联网搜索吗", 2, NOW + timedelta(days=1))
    store.learn(mock_topics([one, one]), [one])
    assert await store.poll() is None and state["fetches"] == 0
    state["now"] += timedelta(days=1)
    store = reopen()
    store.learn(mock_topics([two]), [two])
    assert store.state()["topics"][0]["slow"] is True
    assert await store.poll() is None and state["fetches"] == 1
    store.change("gemini", "pause")
    three = line("Gemini 的新价格怎么样", 3, state["now"])
    store.learn(mock_topics([one, two, three]), [one, two, three])
    assert store.state()["topics"][0]["state"] == "muted"
    explicit = line("以后 Gemini 有新模型跟我说", 4, state["now"])
    store.learn(mock_topics([explicit]), [explicit])
    assert store.state()["topics"][0]["slow"] is False


async def test_baseline_pending_reopen_and_receipts(source):
    store, state, model, reopen = source
    follow = line("以后 DeepSeek 有更新告诉我")
    store.learn(mock_topics([follow]), [follow])
    assert await store.poll() is None and model.calls == 0
    state["contents"] = "目录\n时间: 2026-11-25\n新增的模型\n时间: 2026-11-20\n旧模型"
    assert await store.poll() is None and state["fetches"] == 1
    state["now"] += timedelta(seconds=INTERVAL)
    report = await store.poll()
    assert report is not None and report["source"] == "deepseek" and model.calls == 1
    # 免打扰期间没有回执，服务重启也要保留。
    store = reopen()
    assert await store.poll() == report
    store.shown(report["id"])
    assert await reopen().poll() is None
    assert store.state()["reports"][0]["status"] == "shown"
    state["now"] += timedelta(seconds=DAY+1)
    assert await store.poll() is None and model.calls == 1


async def test_failed_summary_retries_without_advancing_baseline(source):
    store, state, model, _ = source
    follow = line("以后 Gemini 出新模型告诉我")
    store.learn(mock_topics([follow]), [follow])
    await store.poll()
    state["contents"] = "时间: 2026-11-25\n新增的模型"
    model.error = True
    state["now"] += timedelta(seconds=INTERVAL)
    assert await store.poll() is None
    model.error = False
    state["now"] += timedelta(seconds=INTERVAL)
    assert await store.poll()
