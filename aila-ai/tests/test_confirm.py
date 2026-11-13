"""工具确认：会改东西的工具先问用户，同意才执行；不同意、超时、回复被取消都不执行"""

import asyncio
import json
from typing import Any

import httpx
import pytest

from app.agent import NOT_DONE, run_agent
from app.confirm import Confirmations, Pending
from app.llm import MockModel, ToolCall
from app.main import app, get_confirmations, get_model, get_registry
from app.tools import Skill, SkillRegistry, Tool, Toolbox, format_args
from app.tools.browser import browser_skill, check_url

from .test_agent import ScriptedModel
from .test_chat import parse_sse


class Recorder:
    """记录执行过的写操作"""

    def __init__(self) -> None:
        self.done: list[dict[str, Any]] = []

    async def send(self, args: dict[str, Any]) -> str:
        self.done.append(args)
        return "发出去了"


def mail_toolbox(rec: Recorder) -> Toolbox:
    registry = SkillRegistry([Skill("mail", "邮件", "", [
        Tool("send_mail", "", {"type": "object"}, rec.send, lambda _: "正在发邮件", risk="write", action="发一封邮件"),
    ])])
    return registry.toolbox()


SEND = ToolCall("c1", "send_mail", json.dumps({"to": "boss@example.com", "body": "我今天请假"}, ensure_ascii=False))


async def drive(model: Any, toolbox: Toolbox, confirms: Confirmations, answer: bool | None) -> list[dict[str, str]]:
    """跑一轮对话；遇到 confirm 事件时按 answer 回答（None 表示不回答，等超时）"""
    events = []
    async for ev in run_agent(model, [{"role": "user", "content": "帮我发邮件"}], toolbox, confirms):
        events.append(ev)
        if ev["type"] == "confirm" and answer is not None:
            assert confirms.answer(ev["id"], answer)
    return events


@pytest.mark.anyio
async def test_approved_write_runs_after_asking() -> None:
    rec = Recorder()
    model = ScriptedModel([["[nod]好。", SEND], ["发好了。"]])
    events = await drive(model, mail_toolbox(rec), Confirmations(), True)

    ask = next(e for e in events if e["type"] == "confirm")
    assert ask["skill"] == "邮件"
    assert ask["action"] == "发一封邮件"
    # 给用户看的是实际参数，不是大模型自己的说法
    assert ask["detail"] == "to：boss@example.com\nbody：我今天请假"
    types = [e["type"] for e in events]
    assert types.index("confirm") < types.index("confirm_end") < types.index("status")
    assert {"type": "confirm_end", "id": ask["id"], "result": "approved"} in events
    assert rec.done == [{"to": "boss@example.com", "body": "我今天请假"}]
    assert model.calls[1][0][-1]["content"] == "发出去了"


@pytest.mark.anyio
async def test_rejected_write_does_not_run() -> None:
    rec = Recorder()
    model = ScriptedModel([[SEND], ["好的，不发了。"]])
    events = await drive(model, mail_toolbox(rec), Confirmations(), False)

    assert rec.done == []
    assert not any(e["type"] == "status" for e in events)  # 没有「正在发邮件」
    assert next(e for e in events if e["type"] == "confirm_end")["result"] == "rejected"
    assert model.calls[1][0][-1]["content"] == NOT_DONE["rejected"]


@pytest.mark.anyio
async def test_no_answer_times_out() -> None:
    rec = Recorder()
    confirms = Confirmations(timeout=0.05)
    model = ScriptedModel([[SEND], ["那我先不发了。"]])
    events = await drive(model, mail_toolbox(rec), confirms, None)

    assert rec.done == []
    assert next(e for e in events if e["type"] == "confirm_end")["result"] == "timeout"
    assert model.calls[1][0][-1]["content"] == NOT_DONE["timeout"]
    assert confirms.waiting == 0


@pytest.mark.anyio
async def test_cancelled_reply_drops_the_pending_confirmation() -> None:
    """用户打断（桌面端关掉流）：等着的确认作废，之后再点「好」也没用"""
    rec = Recorder()
    confirms = Confirmations()
    gen = run_agent(ScriptedModel([[SEND]]), [{"role": "user", "content": "x"}], mail_toolbox(rec), confirms)
    ask = None
    async for ev in gen:
        if ev["type"] == "confirm":
            ask = ev
            break
    assert ask is not None and confirms.waiting == 1
    await gen.aclose()

    assert confirms.waiting == 0
    assert not confirms.answer(ask["id"], True)
    assert rec.done == []


@pytest.mark.anyio
async def test_without_a_way_to_ask_writes_are_refused() -> None:
    rec = Recorder()
    model = ScriptedModel([[SEND], ["好"]])
    events = [e async for e in run_agent(model, [{"role": "user", "content": "x"}], mail_toolbox(rec))]

    assert not any(e["type"] == "confirm" for e in events)
    assert rec.done == []
    assert "需要用户确认" in model.calls[1][0][-1]["content"]


@pytest.mark.anyio
async def test_invalid_arguments_are_not_put_to_the_user() -> None:
    model = ScriptedModel([[ToolCall("c1", "open_url", '{"url": "file:///C:/Windows/System32/calc.exe"}')], ["好"]])
    events = await drive(model, SkillRegistry([browser_skill(lambda _: True)]).toolbox(), Confirmations(), True)

    assert not any(e["type"] == "confirm" for e in events)
    assert model.calls[1][0][-1]["content"] == "只能打开 http:// 或 https:// 开头的网页。"


@pytest.mark.anyio
async def test_read_tools_never_ask() -> None:
    async def read(_: dict[str, Any]) -> str:
        return "内容"

    box = SkillRegistry([Skill("r", "只读", "", [Tool("look", "", {"type": "object"}, read)])]).toolbox()
    events = await drive(ScriptedModel([[ToolCall("c", "look", "{}")], ["好"]]), box, Confirmations(), None)
    assert not any(e["type"] == "confirm" for e in events)


def test_answers_only_count_once() -> None:
    async def scenario() -> None:
        confirms = Confirmations()
        assert not confirms.answer("nobody", True)
        pending = confirms.open()
        assert confirms.answer(pending.id, False)
        assert not confirms.answer(pending.id, True)  # 改主意也晚了
        assert await confirms.wait(pending) == "rejected"
        assert confirms.waiting == 0

    asyncio.run(scenario())


def test_long_arguments_are_shortened() -> None:
    detail = format_args({"body": "字" * 500, "n": 3, "tags": ["a", "b"]})
    lines = detail.splitlines()
    assert lines[0].endswith("…") and len(lines[0]) < 220
    assert lines[1] == "n：3"
    assert lines[2] == 'tags：["a", "b"]'
    assert format_args({}) == "（没有参数）"


# ---------- 打开网页 ----------


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://github.com", True),
        ("http://localhost:5173/", True),
        ("file:///C:/Windows/System32/calc.exe", False),
        ("javascript:alert(1)", False),
        ("ms-settings:privacy", False),
        ("https://", False),
        ("https://exa mple.com", False),
        ("", False),
    ],
)
def test_only_web_pages_can_be_opened(url: str, ok: bool) -> None:
    assert (check_url({"url": url}) is None) == ok


@pytest.mark.anyio
async def test_open_url_uses_the_browser_after_approval() -> None:
    opened: list[str] = []
    box = SkillRegistry([browser_skill(lambda u: opened.append(u) is None)]).toolbox()
    call = ToolCall("c", "open_url", '{"url": " https://github.com "}')

    ask = box.confirmation(call)
    assert ask is not None and ask.skill == "浏览器" and ask.detail == "https://github.com"
    assert "需要用户确认" in await box.run(call)
    assert opened == []
    assert await box.run(call, approved=True) == "已经在用户的浏览器里打开了 https://github.com"
    assert opened == ["https://github.com"]


# ---------- 接口：/chat 推送 confirm，/chat/confirm 回答 ----------


class SpyConfirmations(Confirmations):
    """测试里没法边读流边回答（ASGITransport 会等回复结束），从这里拿到 id"""

    def __init__(self) -> None:
        super().__init__()
        self.opened = asyncio.Queue[str]()

    def open(self) -> Pending:
        pending = super().open()
        self.opened.put_nowait(pending.id)
        return pending


@pytest.mark.anyio
@pytest.mark.parametrize("approved", [True, False])
async def test_confirm_endpoint_end_to_end(approved: bool) -> None:
    opened: list[str] = []
    confirms = SpyConfirmations()
    app.dependency_overrides[get_model] = lambda: MockModel(delay=0)
    app.dependency_overrides[get_registry] = lambda: SkillRegistry([browser_skill(lambda u: opened.append(u) is None)])
    app.dependency_overrides[get_confirmations] = lambda: confirms
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            chat = asyncio.create_task(
                client.post("/chat", json={"messages": [{"role": "user", "content": "帮我打开 https://github.com"}]})
            )
            confirm_id = await asyncio.wait_for(confirms.opened.get(), 5)
            res = await client.post("/chat/confirm", json={"id": confirm_id, "approved": approved})
            assert res.status_code == 200
            events = parse_sse((await asyncio.wait_for(chat, 5)).text)
            # 已经结束的确认再回答：404
            again = await client.post("/chat/confirm", json={"id": confirm_id, "approved": True})
            assert again.status_code == 404
    finally:
        app.dependency_overrides.clear()

    ask = next(e for e in events if e["type"] == "confirm")
    assert ask == {"type": "confirm", "id": confirm_id, "skill": "浏览器", "action": "在浏览器里打开这个网页", "detail": "https://github.com"}
    text = "".join(e.get("text", "") for e in events if e["type"] == "text")
    if approved:
        assert opened == ["https://github.com"]
        assert "打开了" in text
    else:
        assert opened == []
        assert "不打开了" in text
    assert events[-1] == {"type": "done"}


@pytest.mark.anyio
async def test_confirm_endpoint_rejects_other_websites() -> None:
    """别的网页（比如用户浏览器里打开的恶意网站）不能替用户点「好」"""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post("/chat/confirm", json={"id": "x", "approved": True}, headers={"Origin": "https://evil.example"})
    assert res.status_code == 403
