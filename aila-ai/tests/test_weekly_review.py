"""每周回顾：材料（记忆库里的经历 + 桌面端送来的）、有没有大模型时怎么写、一周一份可以重写、接口"""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import deps
from app.llm import MockModel
from app.main import app, get_model
from app.memory.store import MemoryStore
from app.review import Reviews, WeekMaterial, plain_review


class JsonModel:
    name = "json"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.seen = ""

    async def complete(self, messages: list[dict[str, Any]]) -> str:
        self.seen = messages[-1]["content"]
        return self.reply


def week(store: MemoryStore) -> WeekMaterial:
    today = datetime.now().strftime("%Y-%m-%d")
    store.add_episode("C:\\work\\shop", "让她做：修购物车总价；改了 src/cart.py；最后说：改好了", {"source": "task"})
    store.add_episode("", "用户和艾拉商量了周末爬山", {"source": "chat"})
    return WeekMaterial("2026-W48", today, today, [{"title": "加运费", "project": "C:\\work\\shop", "done": True},
                                                  {"title": "重构登录", "project": "C:\\work\\auth", "done": False}],
                        chats=37, upcoming=["周一 10 点和产品对需求"])


def test_material_from_store_and_desktop(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "m.sqlite3", lambda: None)
    r = Reviews(tmp_path, lambda: MockModel(delay=0), lambda: store)
    lines = r.material(week(store))
    assert "- 编程任务（shop）：加运费，做完了" in lines and "- 编程任务（auth）：重构登录，还没做完" in lines
    assert any(x.startswith("- 任务里的一轮（shop）：让她做：修购物车总价") for x in lines)
    assert "- 聊过的：用户和艾拉商量了周末爬山" in lines and "- 这周聊了 37 句" in lines
    assert "- 下周约好的：周一 10 点和产品对需求" in lines
    # 这周以外的经历不算
    old = WeekMaterial("2020-W01", "2020-01-01", "2020-01-05")
    assert not any("修购物车" in x for x in r.material(old))


def test_plain_review_has_three_parts() -> None:
    text = plain_review(["- 编程任务（shop）：加运费，做完了", "- 编程任务（auth）：重构登录，还没做完", "- 下周约好的：周一开会"])
    assert text.index("## 这周做了什么") < text.index("## 还没做完") < text.index("## 下周")
    assert "加运费" in text and "重构登录" in text and "- 周一开会" in text
    assert "这周没怎么找我" in plain_review([]) and "还没有安排" in plain_review([])


@pytest.mark.anyio
async def test_model_writes_once_a_week_and_can_rewrite(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "m.sqlite3", lambda: None)
    good = "## 这周做了什么\n- shop：加了运费\n## 还没做完\n- 登录重构\n## 下周\n- 周一对需求\n这周辛苦啦。"
    model = JsonModel(json.dumps({"markdown": good}, ensure_ascii=False))
    r = Reviews(tmp_path, lambda: model, lambda: store)
    m = week(store)
    first = await r.write(m)
    assert first["markdown"] == good and first["week"] == "2026-W48"
    assert "<这一周" in model.seen and "加运费" in model.seen
    model.reply = json.dumps({"markdown": good.replace("运费", "包邮")}, ensure_ascii=False)
    assert (await r.write(m))["markdown"] == good  # 写过了不改
    assert "包邮" in (await r.write(m, force=True))["markdown"]  # 要重写才改
    model.reply = "不是 JSON"
    assert (await r.write(m, force=True))["markdown"].startswith("## 这周做了什么\n- 编程任务")  # 写不成就用朴素的
    assert [e["week"] for e in r.entries()] == ["2026-W48"]


def test_routes(tmp_path: Path) -> None:
    app.dependency_overrides[get_model] = lambda: MockModel(delay=0)
    try:
        with TestClient(app) as client:
            today = datetime.now().strftime("%Y-%m-%d")
            body = {"week": "2026-W48", "start": today, "end": today, "tasks": [{"title": "加运费", "done": True}], "chats": 3}
            res = client.post("/studio/reviews", json=body)
            assert res.status_code == 201 and "加运费" in res.json()["markdown"]
            assert [e["week"] for e in client.get("/studio/reviews").json()] == ["2026-W48"]
            assert client.post("/studio/reviews", json={**body, "week": "下周"}).status_code == 409
            assert (deps.settings.data_dir / "reviews" / "2026-W48.json").exists()
    finally:
        app.dependency_overrides.clear()
        time.sleep(0)
