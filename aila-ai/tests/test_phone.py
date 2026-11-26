"""手机连接（局域网）：配对码、设备密钥、门上只放几件事、开任务只能在登记过的项目里、开关真的开关端口"""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from app import deps
from app.main import app
from app.phone import PAIR_TRIES, PhoneServer, PhoneStore, lan_addresses
from app.phone.gateway import create_gateway
from app.runtime.runner import Runtime

from .test_runtime import SHELL, Scripted


def test_pairing_code_and_device_tokens(tmp_path: Path) -> None:
    store = PhoneStore.load(tmp_path / "phone.json")
    assert store.pair("123456", "x") is None  # 还没拿配对码
    p = store.new_pairing(now=1000)
    assert len(p.code) == 6 and p.code.isdigit()
    assert store.pair(p.code, "我的手机", now=1000 + 301) is None  # 过期了
    assert store.pairing is None

    p = store.new_pairing(now=1000)
    wrong = "000000" if p.code != "000000" else "111111"
    for _ in range(PAIR_TRIES):
        assert store.pair(wrong, "x", now=1001) is None
    assert store.pairing is None and store.pair(p.code, "x", now=1001) is None  # 输错太多次：作废

    p = store.new_pairing(now=1000)
    token = store.pair(p.code, "我的手机", now=1001)
    assert token and store.pairing is None  # 一个码只能配一台
    assert store.device(token).name == "我的手机"
    assert store.device("nope") is None and store.device("") is None
    saved = (tmp_path / "phone.json").read_text(encoding="utf-8")
    assert token not in saved  # 只存哈希

    again = PhoneStore.load(tmp_path / "phone.json")
    assert again.device(token) is not None
    assert again.forget(again.devices[0].id) and again.device(token) is None


class Recorder:
    def __init__(self) -> None:
        self.chats: list[list[dict[str, str]]] = []
        self.answers: list[tuple[str, bool]] = []

    async def chat(self, messages: list[dict[str, str]]) -> StreamingResponse:
        self.chats.append(messages)

        async def body():  # noqa: ANN202
            yield 'data: {"type": "text", "text": "你好"}\n\n'
            yield 'data: {"type": "done"}\n\n'

        return StreamingResponse(body(), media_type="text/event-stream")

    def answer(self, cid: str, approved: bool) -> bool:
        self.answers.append((cid, approved))
        return cid == "c1"


@pytest.fixture
def phone(tmp_path: Path) -> tuple[TestClient, PhoneStore, Runtime, Recorder, Path]:
    store = PhoneStore.load(tmp_path / "phone.json")
    store.enabled = True
    rt = Runtime(tmp_path / "data", lambda: Scripted([["看过了。"]]), shell=SHELL)
    project = tmp_path / "shop"
    project.mkdir()
    rec = Recorder()
    client = TestClient(create_gateway(store, lambda: rt, rec.chat, rec.answer))
    return client, store, rt, rec, project


def paired(client: TestClient, store: PhoneStore) -> dict[str, str]:
    code = store.new_pairing().code
    token = client.post("/pair", json={"code": code, "name": "测试手机"}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_page_and_auth(phone: Any) -> None:
    client, store, *_ = phone
    page = client.get("/")
    assert page.status_code == 200 and "艾拉" in page.text
    assert "default-src 'self'" in page.headers["content-security-policy"]
    assert "access-control-allow-origin" not in page.headers
    assert client.get("/app.js").status_code == 200
    # 没配对、密钥不对：一律 401
    for path in ("/api/me", "/api/tasks", "/api/projects"):
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post("/pair", json={"code": "12345", "name": "x"}).status_code == 422
    assert client.post("/pair", json={"code": "123456", "name": "x"}).status_code == 403
    auth = paired(client, store)
    assert client.get("/api/me", headers=auth).json() == {"name": "测试手机"}
    # 本机接口（设置、同意她改文件跑命令）在门上根本没有
    for path in ("/agent/confirm", "/settings", "/phone", "/api/confirm"):
        assert client.post(path, headers=auth, json={}).status_code in (404, 405)


def test_chat_through_the_phone(phone: Any) -> None:
    client, store, _, rec, _ = phone
    auth = paired(client, store)
    res = client.post("/api/chat", headers=auth, json={"messages": [{"role": "user", "content": "在吗"}]})
    assert res.status_code == 200 and '"text": "你好"' in res.text
    assert rec.chats == [[{"role": "user", "content": "在吗"}]]
    assert client.post("/api/chat", headers=auth, json={"messages": [{"role": "assistant", "content": "x"}]}).status_code == 422
    assert client.post("/api/chat", headers=auth, json={"messages": [{"role": "system", "content": "x"}]}).status_code == 422
    assert client.post("/api/chat/confirm", headers=auth, json={"id": "c1", "approved": True}).status_code == 200
    assert client.post("/api/chat/confirm", headers=auth, json={"id": "gone", "approved": True}).status_code == 404


def test_tasks_only_in_known_projects(phone: Any, tmp_path: Path) -> None:
    client, store, rt, _, project = phone
    auth = paired(client, store)
    assert client.get("/api/projects", headers=auth).json() == []
    # 手机不能随便指一个文件夹
    assert client.post("/api/tasks", headers=auth, json={"project": str(project), "text": "看看"}).status_code == 400
    rt.create(str(project), "auto")  # 在电脑上做过任务的项目
    projects = client.get("/api/projects", headers=auth).json()
    assert [p["name"] for p in projects] == ["shop"]
    res = client.post("/api/tasks", headers=auth, json={"project": projects[0]["path"], "text": "看看"})
    assert res.status_code == 201
    sid = res.json()["id"]
    assert res.json()["mode"] == "ask"  # 手机上开的固定先问再改
    listed = client.get("/api/tasks", headers=auth).json()
    assert sid in [s["id"] for s in listed]
    detail = client.get(f"/api/tasks/{sid}", headers=auth).json()
    assert detail["events"][0]["type"] == "start"
    assert client.get("/api/tasks/a.b", headers=auth).status_code == 404
    assert client.get("/api/tasks/nope", headers=auth).status_code == 404


def test_local_settings_api(monkeypatch: pytest.MonkeyPatch) -> None:
    applied: list[bool] = []

    async def fake_apply() -> None:
        applied.append(deps.get_phone().enabled)

    monkeypatch.setattr(deps, "apply_phone", fake_apply)
    client = TestClient(app)
    status = client.get("/phone").json()
    assert status["enabled"] is False and status["devices"] == [] and status["addresses"] == []
    assert client.post("/phone/pair").status_code == 409  # 没打开不给配对码
    assert client.put("/phone", json={"enabled": True}).json()["enabled"] is True
    assert applied == [True]
    code = client.post("/phone/pair").json()["code"]
    assert client.get("/phone").json()["pairing"]["code"] == code
    client.put("/phone", json={"skills": {"web": False}})
    assert deps.get_phone().skills == {"web": False}
    assert client.delete("/phone/devices/nope").status_code == 404
    # 别的网页不能拨这个开关（和别的本机接口一样）
    assert client.put("/phone", json={"enabled": True}, headers={"Origin": "https://evil.example"}).status_code == 403


def test_desktop_chat_remembers_skill_switches() -> None:
    client = TestClient(app)
    client.post("/chat", json={"messages": [{"role": "user", "content": "你好"}], "skills": {"browser": False}})
    assert deps.get_phone().skills == {"browser": False}


@pytest.mark.anyio
async def test_server_really_opens_and_closes_the_port(tmp_path: Path) -> None:
    store = PhoneStore.load(tmp_path / "phone.json")
    rt = Runtime(tmp_path / "data", lambda: Scripted([["好"]]), shell=SHELL)
    rec = Recorder()
    server = PhoneServer(_free_port(), host="127.0.0.1")  # 测试只开在本机，不弹防火墙
    await server.start(create_gateway(store, lambda: rt, rec.chat, rec.answer))
    assert server.running
    async with httpx.AsyncClient() as c:
        res = await c.get(f"http://127.0.0.1:{server.port}/api/me")
        assert res.status_code == 401
    await server.stop()
    assert not server.running
    with pytest.raises(httpx.ConnectError):
        async with httpx.AsyncClient() as c:
            await c.get(f"http://127.0.0.1:{server.port}/")
    assert all(not a.startswith("127.") for a in lan_addresses())


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_phone_page_never_uses_inner_html() -> None:
    js = (Path(__file__).resolve().parents[1] / "app" / "phone" / "static" / "app.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js and "eval(" not in js
    assert json.dumps("textContent") in json.dumps(js) or "textContent" in js
