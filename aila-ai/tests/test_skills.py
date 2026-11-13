"""技能包框架：开关、权限检查、对话时只给开着的工具"""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.llm import ToolCall
from app.main import app, get_model, get_registry
from app.tools import Skill, SkillRegistry, Tool, ToolError


async def echo(args: dict[str, Any]) -> str:
    return f"echo {args.get('x')}"


async def broken(args: dict[str, Any]) -> str:
    raise RuntimeError("bug")


async def refuses(args: dict[str, Any]) -> str:
    raise ToolError("不行")


def make_registry(has_docs: bool = True) -> SkillRegistry:
    return SkillRegistry([
        Skill("web", "联网", "", [Tool("web_search", "", {"type": "object"}, echo)]),
        Skill("knowledge", "资料库", "", [Tool("search_knowledge", "", {"type": "object"}, echo)], available=lambda: has_docs),
        Skill("mcp:mail", "邮件", "", [Tool("mail__read", "", {"type": "object"}, echo),
                                       Tool("mail__send", "", {"type": "object"}, echo, risk="write")], default_enabled=False),
        Skill("misc", "杂项", "", [Tool("broken", "", {"type": "object"}, broken), Tool("refuses", "", {"type": "object"}, refuses)]),
    ])


def call(name: str, args: str = '{"x": 1}') -> ToolCall:
    return ToolCall("c", name, args)


def test_defaults_and_overrides() -> None:
    r = make_registry()
    assert r.toolbox().names == ["web_search", "search_knowledge", "broken", "refuses"]
    assert r.toolbox({"web": False, "mcp:mail": True}).names == ["search_knowledge", "mail__read", "mail__send", "broken", "refuses"]


def test_unavailable_skill_is_hidden() -> None:
    assert "search_knowledge" not in make_registry(has_docs=False).toolbox().names


@pytest.mark.anyio
async def test_disabled_tools_are_refused_even_if_the_model_asks() -> None:
    box = make_registry().toolbox({"web": False})
    assert await box.run(call("web_search")) == "这个能力被用户关掉了，现在用不了。"
    assert await box.run(call("made_up")) == "没有叫 made_up 的工具。"
    assert await box.run(call("search_knowledge")) == "echo 1"


@pytest.mark.anyio
async def test_write_tools_need_confirmation() -> None:
    box = make_registry().toolbox({"mcp:mail": True})
    assert await box.run(call("mail__read")) == "echo 1"
    assert "需要用户确认" in await box.run(call("mail__send"))


@pytest.mark.anyio
async def test_tool_failures_become_words() -> None:
    box = make_registry().toolbox()
    assert await box.run(call("refuses")) == "不行"
    assert await box.run(call("broken")) == "工具出错了（RuntimeError）。"
    assert await box.run(call("web_search", "{bad json")) == "工具参数格式不对，请重新调用。"


def test_describe_for_the_menu() -> None:
    items = {s["id"]: s for s in make_registry(has_docs=False).describe({"misc": False})}
    assert items["web"]["enabled"] and items["web"]["available"]
    assert not items["knowledge"]["available"]
    assert not items["mcp:mail"]["enabled"]
    assert not items["misc"]["enabled"]
    assert items["mcp:mail"]["tools"] == [{"name": "mail__read", "risk": "read"}, {"name": "mail__send", "risk": "write"}]


class ToolsRecorder:
    name = "rec"

    def __init__(self) -> None:
        self.tools: list[str] = []

    async def stream(self, messages: list[dict[str, Any]], tools: Any = None) -> AsyncIterator[str | ToolCall]:
        self.tools = [t["function"]["name"] for t in tools or []]
        yield "好"


def test_chat_only_offers_enabled_tools() -> None:
    model = ToolsRecorder()
    app.dependency_overrides[get_model] = lambda: model
    app.dependency_overrides[get_registry] = make_registry
    try:
        client = TestClient(app)
        client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}], "skills": {"web": False, "misc": False}})
        assert model.tools == ["search_knowledge"]
        assert [s["id"] for s in client.get("/skills").json()] == ["web", "knowledge", "mcp:mail", "misc"]
    finally:
        app.dependency_overrides.clear()
