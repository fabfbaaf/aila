"""手机上同意编程任务的操作、提醒和推送：普通的能在手机上点，危险的只能在电脑上；做完、等你同意时提醒；推出去的不带命令"""

import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import deps
from app.llm import ToolCall
from app.main import app
from app.notify import Notifier, check_push_url, push_request
from app.phone import PhoneStore
from app.phone.gateway import create_gateway, phone_can_answer
from app.runtime.runner import Runtime

from .test_phone import Recorder
from .test_runtime import SHELL, Scripted


def call(name: str, **args: Any) -> ToolCall:
    return ToolCall("c1", name, json.dumps(args, ensure_ascii=False))


def confirm_events(name: str, kind: str, **args: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    assistant = {"type": "assistant", "tool_calls": [{"id": "c1", "name": name, "arguments": json.dumps(args)}]}
    confirm = {"type": "confirm", "id": "x", "call_id": "c1", "tool": name, "kind": kind}
    return [{"type": "start", "kind": "project"}, assistant, confirm], confirm


def test_what_the_phone_may_approve() -> None:
    assert phone_can_answer(*confirm_events("edit_file", "edit", path="a.py"))
    assert phone_can_answer(*confirm_events("run_command", "command", command="npm test"))
    assert not phone_can_answer(*confirm_events("run_command", "command", command="git push --force origin main"))
    assert not phone_can_answer(*confirm_events("run_command", "command", command="type .env"))
    assert not phone_can_answer(*confirm_events("read_file", "secret", path=".env"))
    assert not phone_can_answer(*confirm_events("mcp__db__drop", "mcp"))
    events, confirm = confirm_events("edit_file", "edit", path="a.py")
    events[0]["kind"] = "computer"
    assert not phone_can_answer(events, confirm)  # 电脑任务：只在电脑上


def wait_for(client: TestClient, path: str, auth: dict[str, str], pred: Any, seconds: float = 10) -> dict[str, Any]:
    end = time.time() + seconds
    while time.time() < end:
        data = client.get(path, headers=auth).json()
        if pred(data):
            return data
        time.sleep(0.05)
    raise AssertionError(f"等不到：{path}")


def setup(tmp_path: Path, steps: list[list[Any]]) -> tuple[TestClient, dict[str, str], Runtime, Path]:
    store = PhoneStore.load(tmp_path / "phone.json")
    store.enabled = True
    rt = Runtime(tmp_path / "data", lambda: Scripted(steps), shell=SHELL)
    project = tmp_path / "shop"
    project.mkdir()
    rt.create(str(project), "auto")  # 做过任务的项目
    rec = Recorder()
    client = TestClient(create_gateway(store, lambda: rt, rec.chat, rec.answer))
    client.__enter__()
    code = store.new_pairing().code
    token = client.post("/pair", json={"code": code, "name": "测试手机"}).json()["token"]
    return client, {"Authorization": f"Bearer {token}"}, rt, project


def test_approve_an_edit_from_the_phone(tmp_path: Path) -> None:
    client, auth, rt, project = setup(tmp_path, [[call("write_file", path="a.txt", content="hi\n")], ["写好了。"]])
    try:
        sid = client.post("/api/tasks", headers=auth, json={"project": str(project), "text": "写个文件"}).json()["id"]
        detail = wait_for(client, f"/api/tasks/{sid}", auth, lambda d: any(e["type"] == "confirm" for e in d["events"]))
        confirm = next(e for e in detail["events"] if e["type"] == "confirm")
        assert confirm["phone"] is True and "a.txt" in confirm["detail"]
        assert client.post(f"/api/tasks/{sid}/confirm", headers=auth, json={"id": "nope", "approved": True}).status_code == 404
        assert client.post(f"/api/tasks/{sid}/confirm", headers=auth, json={"id": confirm["id"], "approved": True}).status_code == 200
        wait_for(client, f"/api/tasks/{sid}", auth, lambda d: any(e["type"] == "turn_end" for e in d["events"]))
        assert (project / "a.txt").read_text(encoding="utf-8") == "hi\n"
        end = [e for e in rt.agent(sid).session.events if e["type"] == "confirm_end"][0]
        assert end["result"] == "approved" and end["remember"] is False  # 手机上没有「以后都允许」
    finally:
        client.__exit__(None, None, None)


def test_dangerous_command_only_on_the_computer(tmp_path: Path) -> None:
    client, auth, rt, project = setup(tmp_path, [[call("run_command", command="git push --force origin main")], ["好。"]])
    try:
        sid = client.post("/api/tasks", headers=auth, json={"project": str(project), "text": "推上去"}).json()["id"]
        detail = wait_for(client, f"/api/tasks/{sid}", auth, lambda d: any(e["type"] == "confirm" for e in d["events"]))
        confirm = next(e for e in detail["events"] if e["type"] == "confirm")
        assert confirm["phone"] is False
        res = client.post(f"/api/tasks/{sid}/confirm", headers=auth, json={"id": confirm["id"], "approved": True})
        assert res.status_code == 403 and "电脑" in res.json()["detail"]
        assert rt.confirms.answer(confirm["id"], False)  # 在电脑上拒绝
        wait_for(client, f"/api/tasks/{sid}", auth, lambda d: any(e["type"] == "turn_end" for e in d["events"]))
    finally:
        client.__exit__(None, None, None)


@pytest.mark.anyio
async def test_notifier() -> None:
    sent: list[tuple[str, str, str, str]] = []
    enabled = {"on": False}

    async def sender(kind: str, url: str, title: str, text: str) -> None:
        sent.append((kind, url, title, text))

    n = Notifier(lambda: enabled["on"], lambda: ("ntfy", "https://ntfy.sh/aila-test"), lambda sid: "修登录", sender)
    import asyncio

    stream = n.follow()
    first = asyncio.ensure_future(stream.__anext__())  # 手机页面开着、在收提醒
    await asyncio.sleep(0)
    n.watch("s1", {"type": "confirm", "action": "跑命令 npm test"})  # 没开手机连接：不提醒
    await asyncio.sleep(0)
    assert not first.done()
    assert not sent
    enabled["on"] = True
    n.watch("s1", {"type": "confirm", "action": "跑命令 npm test"})
    got = await asyncio.wait_for(first, 1)
    assert got["kind"] == "confirm" and got["title"] == "修登录" and "npm test" in got["text"]
    n.watch("s1", {"type": "confirm", "action": "跑命令 npm test"})  # 10 秒内同一件事不重复
    n.watch("s1", {"type": "turn_end", "reason": "interrupted"})  # 你自己打断的：不提醒
    n.watch("s1", {"type": "turn_end", "reason": "done"})
    await asyncio.sleep(0.05)
    assert [s[3] for s in sent] == ["等你同意一个操作", "做完了"]
    assert all("npm" not in s[3] for s in sent)  # 推出去的不带命令
    await stream.aclose()


def test_push_formats_and_urls() -> None:
    assert check_push_url("https://ntfy.sh/x") is None
    assert check_push_url("http://192.168.1.5:8080/aila") is None
    assert check_push_url("http://example.com/x") and check_push_url("ftp://x")
    assert push_request("ntfy", "https://ntfy.sh/x", "修登录", "做完了")["content"] == "修登录：做完了".encode()
    assert push_request("bark", "https://api.day.app/K", "t", "x")["json"]["body"] == "t：x"
    assert push_request("wecom", "https://q", "t", "x")["json"] == {"msgtype": "text", "text": {"content": "艾拉：t：x"}}
    assert push_request("feishu", "https://f", "", "x")["json"]["content"]["text"] == "艾拉：x"


def test_push_settings_api(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(app)
    assert client.get("/phone").json()["push"] == {"kind": "none", "set": False}
    assert client.put("/phone", json={"push": {"kind": "wecom", "url": "http://evil.example/hook"}}).status_code == 400
    assert client.put("/phone", json={"push": {"kind": "bark", "url": ""}}).status_code == 400
    status = client.put("/phone", json={"push": {"kind": "bark", "url": "https://api.day.app/SECRETKEY"}}).json()
    assert status["push"] == {"kind": "bark", "set": True} and "SECRETKEY" not in json.dumps(status)  # 不回显地址
    sent: list[tuple[str, str]] = []

    async def fake_send(_client: Any, kind: str, url: str, title: str, text: str) -> None:
        sent.append((kind, url))

    monkeypatch.setattr("app.routes.phone.send_push", fake_send)
    assert client.post("/phone/push-test").json() == {"ok": True}
    assert sent == [("bark", "https://api.day.app/SECRETKEY")]
    client.put("/phone", json={"push": {"kind": "none"}})
    assert deps.get_phone().push_url == "" and client.post("/phone/push-test").status_code == 409
