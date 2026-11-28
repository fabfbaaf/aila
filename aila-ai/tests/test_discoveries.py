"""关注只从用户的话学，官网新内容只入队一次，收到桌面回执才算展示。"""
import json
import sqlite3
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
    category = "releases"
    excerpt = "新增的模型"
    last_request: dict = {}

    async def complete(self, messages):
        self.calls += 1
        if self.error:
            raise TimeoutError()
        if "长期记忆" in messages[0]["content"]:
            return ('{"ops":[{"op":"add","content":"用户喜欢测试","kind":"preference"}],'
                    '"topics":[{"line":1,"source":"gemini","action":"follow","focus":"releases"},'
                    '{"line":2,"source":"deepseek","action":"follow"}]}')
        self.last_request = json.loads(messages[1]["content"])
        assert "新增的模型" in messages[1]["content"]
        return json.dumps({"title": "有新的模型更新", "summary": "官方文档更新了一项模型能力。",
                           "excerpt": self.excerpt, "category": self.category}, ensure_ascii=False)


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
            {"source": "gemini", "name": "Gemini", "state": "active", "focus": "releases", "slow": False,
             "focuses": ["releases"], "delivery": "daily", "origin": "chat", "preference": req["messages"][0]["content"]}
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


async def test_edit_rule_and_history_keep_original_why_and_source(source):
    store, state, model, reopen = source
    preference = "单价有实际下降或有新的网页搜索工具才告诉我"
    rule = {"source": "gemini", "focuses": ["pricing", "web"], "delivery": "history", "preference": preference}
    original = dict(app.dependency_overrides)
    app.dependency_overrides[get_discoveries] = lambda: store
    try:
        client = TestClient(app)
        response = client.put("/discoveries/topics", json=rule)
        assert response.status_code == 200
        topic = response.json()["topics"][0]
        assert topic["focuses"] == ["pricing", "web"] and topic["delivery"] == "history"
        assert topic["origin"] == "manual" and topic["preference"] == preference
        assert client.put("/discoveries/topics", json={**rule, "focuses": []}).status_code == 422
        assert client.put("/discoveries/topics", json={**rule, "source": "unknown"}).status_code == 422
        assert client.put("/discoveries/topics", json={**rule, "preference": "有密码的"}).status_code == 422
        assert client.get("/discoveries/history?before=bad").status_code == 422

        assert await store.poll() is None
        state["now"] += timedelta(seconds=INTERVAL)
        state["contents"] = "时间: 2026-11-25\n新增的模型支持网页搜索\n时间: 2026-11-20\n旧模型"
        model.category = "web"
        # 模型引用旧句子时，保存真实新增的原文。
        model.excerpt = "旧模型"
        assert await store.poll() is None
        assert model.last_request["focuses"] == ["pricing", "web"]
        assert model.last_request["preference"] == preference
        assert "新增的模型" in model.last_request["added_passages"][0]
        report = client.get("/discoveries/history?source=gemini").json()["reports"][0]
        assert report["status"] == "history" and report["shown"] is None
        assert "新增的模型支持网页搜索" in report["excerpt"] and "旧模型" not in report["excerpt"]
        assert report["rule"] == {"origin": "manual", "focuses": ["pricing", "web"], "preference": preference}
        assert report["url"].startswith("https://aistudio.google.com/")

        assert client.put("/discoveries/topics", json={**rule, "focuses": ["releases"], "delivery": "daily"}).status_code == 200
        assert reopen().history(None, None)["reports"] == [report]
        assert client.get("/discoveries/history?source=deepseek").json()["reports"] == []
        assert await reopen().poll() is None
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original)


async def test_a_report_outside_selected_category_is_skipped(source):
    store, state, model, _ = source
    store.save_rule("gemini", ["pricing"], "daily", "")
    await store.poll()
    state["now"] += timedelta(seconds=INTERVAL)
    state["contents"] = "时间: 2026-11-25\n新增的模型"
    model.category = "releases"
    assert await store.poll() is None and model.calls == 1
    assert store.history(None, None)["reports"] == []


async def test_rule_edit_during_fetch_drops_stale_result(source):
    import asyncio

    store, state, model, _ = source
    store.save_rule("gemini", ["releases"], "daily", "")
    await store.poll()
    state["now"] += timedelta(seconds=INTERVAL)
    state["contents"] = "时间: 2026-11-25\n新增的模型"
    started = asyncio.Event()
    finish = asyncio.Event()
    original = store.read

    async def delayed(url):
        started.set()
        await finish.wait()
        return await original(url)

    store.read = delayed
    pending = asyncio.create_task(store.poll())
    await started.wait()
    store.save_rule("gemini", ["pricing"], "history", "只关注降价")
    finish.set()
    assert await pending is None and model.calls == 1
    assert store.history(None, None)["reports"] == []


async def test_migrates_old_records_and_uses_stable_history_cursors(source):
    store, _, _, reopen = source
    with sqlite3.connect(store.path) as db:
        db.executescript("""
            CREATE TABLE topics (source TEXT PRIMARY KEY, state TEXT NOT NULL, focus TEXT NOT NULL,
                preference TEXT NOT NULL, slow INTEGER NOT NULL, checked REAL NOT NULL, snapshot TEXT NOT NULL);
            CREATE TABLE reports (id TEXT PRIMARY KEY, source TEXT NOT NULL, title TEXT NOT NULL,
                summary TEXT NOT NULL, created REAL NOT NULL, status TEXT NOT NULL, shown REAL);
            INSERT INTO topics VALUES ('gemini','active','releases','保留的关注条件',1,0,'保留的基线');
        """)
        db.executemany("INSERT INTO reports VALUES (?,'gemini',?,'Summary',?,'shown',?)", [
            (f"{i:064x}", f"Report {i}", NOW.timestamp(), NOW.timestamp()) for i in (1, 2, 3)
        ])
    topic = reopen().state()["topics"][0]
    assert topic["focuses"] == ["releases"] and topic["delivery"] == "weekly"
    assert topic["preference"] == "保留的关注条件"
    first = store.history(None, None, 2)
    assert [report["title"] for report in first["reports"]] == ["Report 3", "Report 2"]
    assert first["reports"][0]["excerpt"] == "" and first["reports"][0]["rule"]["origin"] == "legacy"
    with store.db() as db:
        assert db.execute("SELECT snapshot FROM topics").fetchone()[0] == "保留的基线"
        db.execute("INSERT INTO reports (id,source,title,summary,created,status) VALUES (?,?,?,?,?,?)",
                   (f"{4:064x}", "gemini", "Report 4", "Summary", NOW.timestamp() + 1, "history"))
    second = store.history(None, first["next_before"], 2)
    assert [report["title"] for report in second["reports"]] == ["Report 1"] and second["next_before"] is None


async def test_multicategory_chat_refinement_preserves_delivery(source):
    store, _, _, _ = source
    store.save_rule("gemini", ["releases"], "history", "")
    turn = line("Gemini 只告诉我降价和联网能力")
    store.learn(mock_topics([turn]), [turn])
    topic = store.state()["topics"][0]
    assert topic["focuses"] == ["pricing", "web"] and topic["delivery"] == "history"
    assert topic["origin"] == "chat" and topic["preference"] == turn["content"]
