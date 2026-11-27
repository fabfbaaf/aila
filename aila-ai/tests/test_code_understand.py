"""看懂项目：一层层往下钻的图（整体架构 → 模块 → 文件接口链路 → 函数）由代码算，AI 只负责讲；出了问题顺着链路讲为什么"""

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.code.explain import ExplainCache, area_facts, chain_facts, explain, parse_answer
from app.code.understand import area, chain, clean, endpoints, entries, hubs, link_text
from app.main import app

from .test_code import make_project


def node(id: str, lines: int = 10, test: bool = False, lang: str = "python") -> dict[str, Any]:
    return {"id": id, "name": id.rsplit("/", 1)[-1], "group": id.split("/", 1)[0], "module": id.rsplit("/", 1)[0],
            "test": test, "lang": lang, "lines": lines, "functions": 2}


def link(s: str, t: str, kind: str = "call", weight: int = 1, via: list[str] | None = None) -> dict[str, Any]:
    return {"source": s, "target": t, "kind": kind, "weight": weight, **({"via": via} if via else {})}


# 一个小项目：桌面端（Electron 三个进程 + shared）、AI 服务、Java 服务端
GRAPH = {
    "nodes": [
        node("desk/src/renderer/App.vue", 200, lang="vue"),
        node("desk/src/renderer/source.ts", 80, lang="typescript"),
        node("desk/src/renderer/i18n.ts", 50, lang="typescript"),
        node("desk/src/preload/index.ts", 40, lang="typescript"),
        node("desk/src/main/index.ts", 300, lang="typescript"),
        node("desk/src/shared/types.ts", 30, lang="typescript"),
        node("ai/app/main.py", 60),
        node("ai/app/routes/agent.py", 150),
        node("ai/app/runtime/runner.py", 400),
        node("ai/app/runtime/session.py", 250),
        node("ai/app/phone/static/app.js", 90, lang="javascript"),
        node("ai/tests/test_runner.py", 100, test=True),
        node("server/src/main/java/com/x/Api.java", 120, lang="java"),
        node("server/src/main/java/com/x/Store.java", 80, lang="java"),
    ],
    "links": [
        link("desk/src/renderer/App.vue", "desk/src/renderer/source.ts"),
        link("desk/src/renderer/App.vue", "desk/src/renderer/i18n.ts", weight=9),
        link("desk/src/renderer/source.ts", "ai/app/routes/agent.py", "http", 2, ["/agent/sessions", "/agent/sessions/{}/messages"]),
        link("desk/src/preload/index.ts", "desk/src/main/index.ts", "ipc", 3, ["agent:list"]),
        link("desk/src/renderer/App.vue", "desk/src/main/index.ts"),  # 按名字连错的：不同进程
        link("desk/src/main/index.ts", "desk/src/shared/types.ts", "import"),
        link("desk/src/renderer/source.ts", "desk/src/shared/types.ts", "import"),
        link("ai/app/phone/static/app.js", "desk/src/renderer/i18n.ts"),  # 按名字连错的：不同程序
        link("ai/app/routes/agent.py", "ai/app/runtime/runner.py", weight=4),
        link("ai/app/runtime/runner.py", "ai/app/runtime/session.py", weight=6),
        link("ai/app/main.py", "ai/app/routes/agent.py", "import"),
        link("ai/tests/test_runner.py", "ai/app/runtime/runner.py", weight=20),
        link("ai/app/runtime/runner.py", "server/src/main/java/com/x/Api.java", "http", 1, ["/api/sync"]),
        link("server/src/main/java/com/x/Api.java", "server/src/main/java/com/x/Store.java", weight=2),
    ],
}


@pytest.fixture
def graph() -> dict[str, Any]:
    return clean(GRAPH)


def pairs(view: dict[str, Any]) -> set[tuple[str, str]]:
    return {(link["source"], link["target"]) for link in view["links"]}


# ---------- 去掉不可能的连线 ----------


def test_calls_never_cross_programs_or_electron_processes(graph: dict[str, Any]) -> None:
    kept = {(link["source"], link["target"]) for link in graph["links"]}
    assert ("desk/src/renderer/App.vue", "desk/src/main/index.ts") not in kept  # renderer 和 main 只能走 IPC
    assert ("ai/app/phone/static/app.js", "desk/src/renderer/i18n.ts") not in kept  # 两个程序只能走 HTTP
    assert ("desk/src/main/index.ts", "desk/src/shared/types.ts") in kept  # shared 几个进程都用
    assert ("desk/src/preload/index.ts", "desk/src/main/index.ts") in kept  # IPC 照常
    assert ("desk/src/renderer/source.ts", "ai/app/routes/agent.py") in kept  # HTTP 照常


# ---------- 一层层往下钻 ----------


def test_root_is_the_overall_architecture(graph: dict[str, Any]) -> None:
    view = area(graph)
    assert view["path"] == "" and view["parent"] is None
    names = [c["name"] for c in view["children"]]
    assert set(names) == {"desk/src", "ai", "server/src/main/java/com/x"}  # 只有一个子目录的一路合并
    assert ("desk/src", "ai") in pairs(view) and ("ai", "server/src/main/java/com/x") in pairs(view)
    desk_ai = next(x for x in view["links"] if x["source"] == "desk/src")
    assert desk_ai["kinds"] == {"http": 2} and desk_ai["via"] == ["/agent/sessions", "/agent/sessions/{}/messages"]
    assert "（/agent/sessions" in link_text(desk_ai) and "HTTP 接口 2 处" in link_text(desk_ai)
    assert view["entries"][0] in ("ai/app/main.py", "desk/src/main/index.ts")


def test_drilling_into_a_folder_shows_children_and_outside(graph: dict[str, Any]) -> None:
    view = area(graph, "ai")
    assert view["path"] == "ai" and view["parent"] == ""
    assert {c["name"] for c in view["children"]} == {"app", "tests"}
    tests_dir = next(c for c in view["children"] if c["name"] == "tests")
    assert tests_dir["onlyTests"]
    assert {o["id"] for o in view["outside"]} == {"desk", "server"}
    assert ("desk", "ai/app") in pairs(view) and ("ai/app", "server") in pairs(view)
    assert ("ai/tests", "ai/app") not in pairs(view)  # 测试不参与连线

    inner = area(graph, "ai/app")
    assert {c["name"] for c in inner["children"]} >= {"routes", "runtime", "main.py", "phone/static"}
    assert ("ai/app/routes", "ai/app/runtime") in pairs(inner) and ("ai/app/main.py", "ai/app/routes") in pairs(inner)
    files = area(graph, "ai/app/runtime")
    assert [c["kind"] for c in files["children"]] == ["file", "file"]
    assert ("ai/app/runtime/runner.py", "ai/app/runtime/session.py") in pairs(files)
    assert ("ai/app/routes", "ai/app/runtime/runner.py") in pairs(files)  # 外面的兄弟目录连进来


def test_tests_can_be_shown(graph: dict[str, Any]) -> None:
    assert ("ai/tests", "ai/app") in pairs(area(graph, "ai", tests=True))


def test_pointing_at_a_file_shows_its_folder(graph: dict[str, Any]) -> None:
    assert area(graph, "ai/app/runtime/runner.py")["path"] == "ai/app/runtime"


def test_entries_and_hubs(graph: dict[str, Any]) -> None:
    assert "ai/app/main.py" in entries(graph, "ai")
    assert hubs(graph, "ai")[0] in ("ai/app/runtime/runner.py", "ai/app/routes/agent.py", "ai/app/runtime/session.py")
    assert "ai/tests/test_runner.py" not in hubs(graph)


# ---------- 文件接口链路 ----------


def test_chain_follows_interfaces_not_utilities(graph: dict[str, Any]) -> None:
    view = chain(graph, start="desk/src/renderer/App.vue", width=1)
    layers = {n["id"]: n["layer"] for n in view["nodes"]}
    # 只能走一条时，走向 HTTP 接口的 source.ts，而不是人人都用、连得更多的 i18n
    assert layers["desk/src/renderer/source.ts"] == 1 and "desk/src/renderer/i18n.ts" not in layers
    assert layers["ai/app/routes/agent.py"] == 2 and layers["ai/app/runtime/runner.py"] == 3
    http = next(x for x in view["links"] if x["source"] == "desk/src/renderer/source.ts")
    assert http["kind"] == "http" and http["via"][0] == "/agent/sessions"


def test_chain_goes_up_to_who_uses_it(graph: dict[str, Any]) -> None:
    view = chain(graph, start="ai/app/runtime/runner.py", down=1, up=2)
    layers = {n["id"]: n["layer"] for n in view["nodes"]}
    assert layers["ai/app/routes/agent.py"] == -1 and layers["ai/app/runtime/session.py"] == 1
    assert "ai/tests/test_runner.py" not in layers


def test_chain_from_an_endpoint_takes_the_exact_one(graph: dict[str, Any]) -> None:
    view = chain(graph, endpoint="/agent/sessions", down=2, up=0)
    layers = {n["id"]: n["layer"] for n in view["nodes"]}
    assert layers["desk/src/renderer/source.ts"] == 0 and layers["ai/app/routes/agent.py"] == 1
    assert "desk/src/shared/types.ts" not in layers  # 第一步只顺着这个接口走
    assert chain(graph, endpoint="/nope")["nodes"] == []
    assert chain(graph, start="no/such.py")["nodes"] == []


def test_endpoints_are_listed(graph: dict[str, Any]) -> None:
    found = {(e["kind"], e["name"]) for e in endpoints(graph)}
    assert ("http", "/agent/sessions") in found and ("ipc", "agent:list") in found and ("http", "/api/sync") in found


# ---------- 讲解 ----------


def test_answer_parsing() -> None:
    assert parse_answer('```json\n{"summary": "做加法", "detail": "两个数相加", "watch": ""}\n```') == \
        {"summary": "做加法", "detail": "两个数相加", "watch": ""}
    assert parse_answer("不是 JSON") is None
    assert parse_answer('{"detail": "没有一句话"}') is None


class FakeModel:
    def __init__(self, reply: str = '{"summary": "它负责开任务", "detail": "收到请求后交给运行时。", "watch": "别在这里做耗时的事"}') -> None:
        self.reply = reply
        self.calls: list[list[dict[str, str]]] = []

    async def complete(self, messages: list[dict[str, str]], max_tokens: int = 600) -> str:
        self.calls.append(messages)
        self.max_tokens = max_tokens
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def test_explain_caches_by_content(tmp_path: Path) -> None:
    cache = ExplainCache(tmp_path / "c.json")
    model = FakeModel()

    async def go() -> list[dict[str, Any]]:
        return [await explain(model, cache, "file", "dev", "事实 A", "代码 A"),
                await explain(model, cache, "file", "dev", "事实 A", "代码 A"),
                await explain(model, cache, "file", "beginner", "事实 A", "代码 A"),
                await explain(model, ExplainCache(tmp_path / "c.json"), "file", "dev", "事实 A", "代码 A")]

    first, second, other_depth, reloaded = asyncio.run(go())
    assert first["explanation"]["summary"] == "它负责开任务" and not first["cached"]
    assert model.max_tokens == 1500  # 讲解给足长度，不然 JSON 写到一半被截断
    assert second["cached"] and reloaded["cached"]  # 同样的内容不重复花钱，重启以后也记得
    assert not other_depth["cached"] and len(model.calls) == 2
    system, user = model.calls[0][0]["content"], model.calls[0][1]["content"]
    assert "开发者" in system and "只根据这些内容讲" in system and "【事实】\n事实 A" in user and "【代码】\n代码 A" in user
    assert "零基础" not in system and "刚开始学编程" in model.calls[1][0]["content"]


def test_explain_without_key_or_on_error_gives_facts_only(tmp_path: Path) -> None:
    cache = ExplainCache(tmp_path / "c.json")
    no_key = asyncio.run(explain(None, cache, "area", "beginner", "事实"))
    assert no_key["explanation"] is None and no_key["facts"] == "事实" and "密钥" in no_key["note"]
    broken = asyncio.run(explain(FakeModel(RuntimeError("额度用完了")), cache, "area", "beginner", "事实"))  # type: ignore[arg-type]
    assert broken["explanation"] is None and "额度用完了" in broken["note"]
    nonsense = asyncio.run(explain(FakeModel("嗯嗯"), cache, "area", "beginner", "事实"))
    assert nonsense["explanation"] is None and not cache.get(cache.key("area", "beginner", "事实", ""))


def test_facts_are_written_from_the_views(graph: dict[str, Any]) -> None:
    facts = area_facts(area(graph), link_text, "# 艾拉\n开源的编程 Agent")
    assert "目录 desk/src" in facts and "HTTP 接口 2 处" in facts and "README 开头" in facts and "开源的编程 Agent" in facts
    text = chain_facts(chain(graph, start="desk/src/renderer/App.vue", width=1))
    assert "起点：desk/src/renderer/App.vue" in text and "往下第 2 步：ai/app/routes/agent.py" in text
    assert "HTTP 接口（/agent/sessions" in text


# ---------- 接口（用 test_code 的小项目） ----------


def wait_ready(client: TestClient) -> None:
    for _ in range(200):
        if client.get("/code/projects").json()[0]["state"] == "ready":
            return
        time.sleep(0.05)


def test_understanding_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = make_project(tmp_path / "proj")
    client = TestClient(app)
    pid = client.post("/code/projects", json={"path": str(root)}).json()["id"]
    wait_ready(client)

    top = client.get(f"/code/projects/{pid}/area").json()
    assert {c["name"] for c in top["children"]} >= {"shop", "web/src"}
    shop = client.get(f"/code/projects/{pid}/area", params={"path": "shop"}).json()
    assert ("shop/checkout.py", "shop/cart.py") in pairs(shop)

    view = client.get(f"/code/projects/{pid}/chain", params={"file": "shop/checkout.py"}).json()
    assert {n["id"] for n in view["nodes"]} == {"shop/checkout.py", "shop/cart.py"}
    assert client.get(f"/code/projects/{pid}/chain", params={"file": "../secret.txt"}).status_code == 404

    detail = client.get(f"/code/projects/{pid}/file", params={"file": "shop/cart.py"}).json()
    assert detail["usedBy"] == ["shop/checkout.py"] and detail["outline"][0]["qname"] == "price_of"
    fn = client.get(f"/code/projects/{pid}/function", params={"file": "shop/cart.py", "line": 19}).json()
    assert fn["qname"] == "Cart.total" and fn["lines"][0].strip() == "def total(self):"
    fn = client.get(f"/code/projects/{pid}/function", params={"file": "shop/cart.py", "line": 8}).json()
    assert fn["qname"] == "price_of" and {c["qname"] for c in fn["callers"]} >= {"Cart.total", "checkout"}

    # 没填密钥：只给事实
    res = client.post(f"/code/projects/{pid}/explain", json={"level": "function", "file": "shop/cart.py", "line": 19}).json()
    assert res["explanation"] is None and "Cart.total" in res["facts"]
    # 填了密钥：事实由服务端重新算，AI 讲解
    model = FakeModel()
    monkeypatch.setattr("app.routes.code._model", lambda: model)
    res = client.post(f"/code/projects/{pid}/explain", json={"level": "area", "path": "shop", "depth": "dev"}).json()
    assert res["explanation"]["summary"] == "它负责开任务" and "shop/checkout.py" in res["facts"]
    assert client.post(f"/code/projects/{pid}/explain", json={"level": "file", "file": "nope.py"}).status_code == 404
    assert client.post(f"/code/projects/{pid}/explain", json={"level": "evil"}).status_code == 422

    trace = f'Traceback (most recent call last):\n  File "{root / "shop" / "cart.py"}", line 8, in price_of\nKeyError: \'apple\''
    diag = client.post(f"/code/projects/{pid}/diagnose", json={"text": trace}).json()
    assert diag["found"] and diag["frames"][0]["qname"] == "price_of" and "shop/cart.py" in diag["files"]
    assert {c["qname"] for c in diag["chain"]} >= {"Cart.total"}
    assert diag["explanation"]["summary"] == "它负责开任务"
    prompt = model.calls[-1]
    assert "KeyError" in prompt[1]["content"] and "def price_of" in prompt[1]["content"] and "报错" in prompt[0]["content"]
    nothing = client.post(f"/code/projects/{pid}/diagnose", json={"text": "就是不对"}).json()
    assert not nothing["found"] and nothing["note"]
