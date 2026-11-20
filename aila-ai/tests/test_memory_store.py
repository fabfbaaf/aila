"""记忆库：事实、经历、项目约定放在一个 SQLite 里，关键词 + 向量检索；对话摘要、编程任务的经历、开头的回想"""

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import deps
from app.llm import MockModel
from app.main import app, get_model
from app.memory.episodes import Digester, Line, chat_context, coding_context, digest, task_episode, undigested
from app.memory.recall import Memory, recall
from app.memory.store import EPISODE, FACT, MAX_EPISODES, PROJECT, MemoryStore
from app.runtime.memory import project_key
from app.runtime.runner import Runtime

from .test_memory import FakeEmbedder, RecordingModel
from .test_runtime import SHELL, Scripted, call, project  # noqa: F401  （project 是 fixture）

NOW = datetime(2026, 11, 19, 10, 0).astimezone()


class Holder:
    """向量模型一开始没加载好，后来才有"""

    def __init__(self) -> None:
        self.embedder: Any = None

    def __call__(self) -> Any:
        return self.embedder


def mem(i: str, content: str, kind: str = "other", **kw: Any) -> Memory:
    return Memory(i, content, kind, updated_at=kw.get("updated_at", NOW), expires_at=kw.get("expires_at"))


# ---------- 库 ----------


def test_facts_follow_the_desktop_list(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "m.sqlite3", lambda: None)
    assert store.sync_facts([mem("a", "用户叫小王", "profile"), mem("b", "用户不吃香菜", "preference")]) == 2
    assert store.sync_facts([mem("a", "用户叫小王", "profile"), mem("b", "用户不吃香菜", "preference")]) == 0  # 没变不重写
    # 改一条、删一条（桌面端删了，这里也要删）
    assert store.sync_facts([mem("a", "用户叫王小明", "profile")]) == 2
    facts = store.facts()
    assert [(m.id, m.content, m.kind) for m in facts] == [("a", "用户叫王小明", "profile")]
    assert store.search("香菜", (FACT,)) == []
    assert store.sync_facts([]) == 1 and store.count(FACT) == 0  # 退出登录、全部忘掉


def test_keyword_and_vector_search_and_backfill(tmp_path: Path) -> None:
    holder = Holder()
    store = MemoryStore(tmp_path / "m.sqlite3", holder)
    store.sync_facts([mem("cat", "用户养了一只叫团子的橘猫"), mem("job", "用户在杭州做后端开发"),
                      mem("err", "用户的项目报错码是 E1024")])
    # 没有向量模型：只靠关键词
    hits = store.search("团子最近怎么样", (FACT,))
    assert [h.entry.id for h in hits] == ["cat"] and hits[0].keyword and hits[0].similarity is None
    # 向量模型加载好了：没向量的补上，按意思也能找到
    holder.embedder = FakeEmbedder()
    hits = store.search("杭州做后端", (FACT,))
    assert hits[0].entry.id == "job" and hits[0].similarity is not None and hits[0].similarity > 0.5
    assert store.search("E1024", (FACT,))[0].entry.id == "err"
    assert store.search("  ", (FACT,)) == [] and store.search("团子", ()) == []


def test_episodes_scopes_and_limits(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "m.sqlite3", lambda: None)
    store.add_episode("", "用户和艾拉聊了周末去爬山", {"source": "chat"})
    store.add_episode("C:\\proj", "让她做：修登录；改了 src/login.py", {"source": "task"})
    store.sync_project("C:\\proj", [("n1", "测试用 pytest -q")])
    assert [e.content for e in store.recent(EPISODE, "", 5, source="chat")] == ["用户和艾拉聊了周末去爬山"]
    assert store.recent(EPISODE, "", 5, source="task") == []
    assert [h.entry.kind for h in store.search("pytest", (EPISODE, PROJECT))] == [PROJECT]
    assert store.search("登录", (EPISODE,), scope="")== []  # 别的范围的不找
    store.sync_project("C:\\proj", [])
    assert store.count(PROJECT) == 0
    for i in range(MAX_EPISODES + 3):
        store.add_episode("x", f"第 {i} 次")
    assert len(store.recent(EPISODE, "x", MAX_EPISODES + 10)) == MAX_EPISODES  # 多了删最早的
    store.mark_digested(["h1", "h2"])
    assert store.digested(["h1", "h3"]) == {"h1"}


def test_recall_with_store_finds_keywords_too(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "m.sqlite3", lambda: None)
    memories = [mem(f"x{i}", f"用户收藏了第{i}张唱片") for i in range(12)]
    memories += [mem("cat", "用户养了一只叫团子的橘猫"), mem("me", "用户是程序员", "profile"),
                 mem("old", "用户有面试", "event", expires_at=NOW - timedelta(days=1))]
    store.sync_facts(memories)
    picked = {m.id for m in recall(memories, "团子今天在家吗", None, NOW, store)}
    # 身份类总带上；没有向量模型时以前只能带最近的几条，现在关键词能找到团子；过期的不要
    assert "me" in picked and "cat" in picked and "old" not in picked and len(picked) <= 8


# ---------- 对话摘要 ----------


class JsonModel:
    name = "json"

    def __init__(self, reply: str = "", fail: bool = False) -> None:
        self.reply, self.fail, self.calls = reply, fail, 0

    async def complete(self, messages: list[dict[str, Any]]) -> str:
        self.calls += 1
        if self.fail:
            raise RuntimeError("连不上")
        return self.reply


def lines(n: int, word: str = "爬山") -> list[Line]:
    return [Line("user" if i % 2 == 0 else "assistant", f"第 {i} 句，说{word}") for i in range(n)]


@pytest.mark.anyio
async def test_digest_once_retry_on_failure_and_drop_sensitive(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "m.sqlite3", lambda: None)
    dropped = lines(10)
    assert await digest(JsonModel(fail=True), store, []) == ""
    with pytest.raises(RuntimeError):
        await digest(JsonModel(fail=True), store, undigested(store, dropped))
    assert len(undigested(store, dropped)) == 10  # 失败了不记指纹，下次再来

    model = JsonModel('{"summary": "用户说周末想去爬山。用户的密码是 abc123。艾拉推荐了路线。"}')
    text = await digest(model, store, undigested(store, dropped))
    assert text == "用户说周末想去爬山。艾拉推荐了路线。"  # 带密码的那句去掉
    assert undigested(store, dropped) == []
    # 再滑出去十句：只摘要新的
    more = dropped + lines(10, "看书")[::-1]
    assert len(undigested(store, more)) == 10
    # 假模型（没有 complete）：用户说过的话拼起来
    assert (await digest(MockModel(delay=0), store, undigested(store, more))).startswith("用户聊到：")
    assert store.count(EPISODE) == 2


@pytest.mark.anyio
async def test_digester_runs_one_at_a_time(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "m.sqlite3", lambda: None)
    d = Digester()
    task = d.schedule(JsonModel('{"summary": "聊了爬山"}'), store, lines(10))
    assert task is not None and d.schedule(JsonModel(), store, lines(10)) is None  # 正在做
    assert await task == "聊了爬山"
    assert d.schedule(JsonModel(), store, lines(10)) is None  # 都摘要过了
    failing = d.schedule(JsonModel(fail=True), store, lines(12)[2:] + lines(2, "x"))
    assert failing is not None and await failing == ""  # 失败只记日志


def test_chat_context_has_latest_digest_and_related_episodes(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "m.sqlite3", lambda: None)
    store.add_episode("", "用户和艾拉商量了去黄山的行程", {"source": "chat"})
    store.add_episode("C:\\shop", "让她做：修购物车总价；改了 src/cart.py", {"source": "task"})
    time.sleep(0.01)
    store.add_episode("", "用户说最近在学吉他", {"source": "chat"})
    text = chat_context(store, [mem("a", "用户叫小王", "profile")], "购物车总价那个问题后来怎样了", None, NOW)
    assert "- 用户叫小王" in text
    assert "更早聊过的" in text and "用户说最近在学吉他" in text  # 最新一份摘要总带上
    assert "（编程任务）让她做：修购物车总价" in text  # 话题相关的经历
    assert "黄山" not in text
    assert [m.id for m in store.facts()] == ["a"]  # 顺便把事实对齐进库


def test_chat_endpoint_digests_what_falls_out_of_the_window() -> None:
    model = RecordingModel()
    calls: list[str] = []

    async def complete(messages: list[dict[str, Any]]) -> str:
        calls.append(messages[-1]["content"])
        return '{"summary": "用户和艾拉聊了怎么养多肉"}'

    model.complete = complete  # type: ignore[attr-defined]
    app.dependency_overrides[get_model] = lambda: model
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"多肉第 {i} 句"} for i in range(51)]
    try:
        with TestClient(app) as client:
            assert client.post("/chat", json={"messages": history}).status_code == 200
            store = deps.get_memory_store()
            for _ in range(100):
                if store.count(EPISODE):
                    break
                time.sleep(0.02)
            assert len(calls) == 1 and "多肉第 0 句" in calls[0] and "多肉第 50 句" not in calls[0]
            history.append({"role": "assistant", "content": "好"})
            history.append({"role": "user", "content": "接着说"})
            assert client.post("/chat", json={"messages": history}).status_code == 200
            assert "用户和艾拉聊了怎么养多肉" in model.last
            time.sleep(0.1)
            assert len(calls) == 1  # 滑出去的还是那些，不重复摘要
    finally:
        app.dependency_overrides.clear()


def test_facts_endpoint_and_extract_failure_is_reported() -> None:
    class Broken:
        name = "broken"

        async def complete(self, messages: Any) -> str:
            raise RuntimeError("timeout")

    with TestClient(app) as client:
        res = client.put("/memory/facts", json={"memories": [{"id": "a", "content": "用户叫小王", "kind": "profile"}]})
        assert res.json() == {"changed": 1, "total": 1}
        assert [m.content for m in deps.get_memory_store().facts()] == ["用户叫小王"]
        app.dependency_overrides[get_model] = lambda: Broken()
        try:
            res = client.post("/memory/extract", json={"messages": [{"role": "user", "content": "记住我喜欢猫"}]})
        finally:
            app.dependency_overrides.clear()
        assert res.status_code == 502  # 告诉桌面端没整理成，它会再试

        # 经历：给你看、给你删；事实不能从这里删
        store = deps.get_memory_store()
        a = store.add_episode("", "用户和艾拉聊了猫", {"source": "chat"})
        store.add_episode("C:\\shop", "让她做：修购物车", {"source": "task"})
        listed = client.get("/memory/episodes").json()
        assert [(e["content"], e["source"], e["project"]) for e in listed] == [
            ("让她做：修购物车", "task", "C:\\shop"), ("用户和艾拉聊了猫", "chat", "")]
        assert client.delete(f"/memory/episodes/{a.id}").json() == {"ok": True}
        assert client.delete("/memory/episodes/a").status_code == 404 and store.count(FACT) == 1
        assert client.delete("/memory/episodes").json() == {"deleted": 1} and store.count(EPISODE) == 0


# ---------- 编程任务 ----------


def test_task_episode_from_events() -> None:
    events = [
        {"type": "start"},
        {"type": "user", "text": "旧的一轮"},
        {"type": "turn_end", "reason": "done"},
        {"type": "user", "text": "把总价加上运费"},
        {"type": "tool", "name": "edit_file", "status": "ok", "file": "src/cart.py"},
        {"type": "tool", "name": "write_file", "status": "ok", "file": "src/cart.py"},
        {"type": "tool", "name": "read_file", "status": "ok", "file": "src/a.py"},
        {"type": "tool", "name": "edit_file", "status": "denied", "file": "src/x.py"},
        {"type": "assistant", "text": "改好了，\n测试都过了。"},
        {"type": "turn_end", "reason": "done"},
    ]
    assert task_episode(events) == "让她做：把总价加上运费；改了 src/cart.py；最后说：改好了， 测试都过了。"
    stopped = events[:5] + [{"type": "turn_end", "reason": "interrupted"}]
    assert task_episode(stopped).endswith("（没做完就停了：interrupted）")
    assert task_episode([{"type": "user", "text": "嗯"}, {"type": "turn_end", "reason": "error"}]) == ""
    staged = [{"type": "user", "text": "开始第 1/2 阶段：……很长的一段", "title": "第 1/2 阶段：读一遍项目"},
              {"type": "assistant", "text": "这一段做完了。"}, {"type": "turn_end", "reason": "done"}]
    assert task_episode(staged) == "让她做：第 1/2 阶段：读一遍项目；最后说：这一段做完了。"


@pytest.mark.anyio
async def test_coding_sessions_remember_and_recall(tmp_path: Path, project: Path) -> None:  # noqa: F811
    store = MemoryStore(tmp_path / "m.sqlite3", lambda: None)
    store.sync_facts([mem("a", "用户喜欢简短的提交信息", "preference"), mem("b", "用户下周三面试", "event")])
    script = [[call("read_file", path="src/cart.py")], [call("edit_file", path="src/cart.py", old_string="return sum(items)", new_string="return sum(items) + 5")],
              ["改好了。"]]
    rt = Runtime(tmp_path / "data", lambda: Scripted(script), shell=SHELL, memory_store=lambda: store)
    rt.memory.add(project, "测试用 pytest -q")  # 已有的项目约定：第一次用记忆库时放进去
    first = rt.create(str(project), "auto")
    assert "关于用户：\n- 用户喜欢简短的提交信息" in first.events[0]["context"] and "面试" not in first.events[0]["context"]
    _ = [e async for e in rt.run(first.id, "总价加五块运费")]
    recent = store.recent(EPISODE, project_key(project), 5, source="task")
    assert [e.content for e in recent] == ["让她做：总价加五块运费；改了 src/cart.py；最后说：改好了。"]
    assert [h.entry.content for h in store.search("pytest", (PROJECT,))] == ["测试用 pytest -q"]

    second = rt.create(str(project), "auto")
    assert "这个项目最近几次：" in second.events[0]["context"] and "总价加五块运费" in second.events[0]["context"]
    assert "改好了" in coding_context(store, project_key(project), NOW)
    rt.memory.remove(project, rt.memory.notes(project)[0].id)
    assert store.count(PROJECT) == 0  # 删了项目记忆，库里也删
    await rt.shutdown()
