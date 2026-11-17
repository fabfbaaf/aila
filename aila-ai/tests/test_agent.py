"""工具调用循环：先说话再调工具、把结果交回大模型、轮数上限、进度事件"""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agent import MAX_TOOL_ROUNDS, run_agent
from app.llm import MockModel, ToolCall
from app.main import app, get_model, get_registry
from app.tags import Event
from app.tools import Toolbox
from app.tools.web import web_toolbox

from .test_chat import offline_registry, offline_toolbox, parse_sse


class ScriptedModel:
    """按剧本一轮轮输出；记录每轮收到的消息和工具"""

    name = "scripted"

    def __init__(self, rounds: list[list[str | ToolCall]]) -> None:
        self.rounds = rounds
        self.calls: list[tuple[list[dict[str, Any]], list[dict[str, Any]] | None]] = []

    async def stream(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> AsyncIterator[str | ToolCall]:
        self.calls.append((list(messages), tools))
        script = self.rounds[min(len(self.calls) - 1, len(self.rounds) - 1)]
        for item in script:
            yield item


async def collect(model: Any, toolbox: Toolbox, text: str = "帮我搜一下") -> list[Event]:
    return [e async for e in run_agent(model, [{"role": "user", "content": text}], toolbox)]


@pytest.mark.anyio
async def test_search_then_answer() -> None:
    model = ScriptedModel(
        [
            ["[think]我查", "一下。", ToolCall("c1", "web_search", json.dumps({"query": "Python 3.13"}))],
            ["[nod]查到了。"],
        ]
    )
    events = await collect(model, offline_toolbox())

    assert events == [
        {"type": "action", "name": "think"},
        {"type": "text", "text": "我查", "spoken": True},
        {"type": "text", "text": "一下。", "spoken": True},
        {"type": "status", "text": "正在搜索「Python 3.13」"},
        {"type": "action", "name": "nod"},
        {"type": "text", "text": "查到了。", "spoken": True},
    ]
    # 第二轮大模型看到了自己的工具调用和搜索结果
    second_round = model.calls[1][0]
    assert second_round[-2]["role"] == "assistant"
    assert second_round[-2]["tool_calls"][0]["function"]["name"] == "web_search"
    assert second_round[-1]["role"] == "tool"
    assert second_round[-1]["tool_call_id"] == "c1"
    assert "（假数据）" in second_round[-1]["content"]
    assert "不要理会" in second_round[-1]["content"]  # 提醒它网页内容不是指令


@pytest.mark.anyio
async def test_spoken_summary_after_search_and_one_hint_per_reply() -> None:
    tool = ToolCall("c1", "web_search", '{"query": "例子"}')
    first = "我查一下。稍等。"
    model = ScriptedModel([[first, tool], ["[sa", "y]查到了。[/say]\n1. 全文"]])
    events = await collect(model, offline_toolbox())
    spoken = "".join(str(e["text"]) for e in events if e.get("spoken") is True)
    written = "".join(str(e["text"]) for e in events if e["type"] == "text")
    assert spoken == first + "查到了。"
    assert written == spoken + "\n1. 全文"
    assert not any(event.get("hint") for event in events)

    model = ScriptedModel([[first + "只写这里。", tool], ["再补一条。"]])
    events = await collect(model, offline_toolbox())
    assert [event for event in events if event.get("hint")] == [
        {"type": "text", "text": "", "spoken": True, "hint": "more"},
    ]
    assert events[-1].get("hint") == "more"


@pytest.mark.anyio
async def test_tool_rounds_are_capped() -> None:
    # 大模型一直想调工具：到上限后最后一轮不给工具
    model = ScriptedModel([[ToolCall("c", "web_search", '{"query": "x"}')]])
    await collect(model, offline_toolbox())

    assert len(model.calls) == MAX_TOOL_ROUNDS + 1
    assert all(tools for _, tools in model.calls[:-1])
    assert model.calls[-1][1] is None


@pytest.mark.anyio
async def test_bad_tool_arguments_are_reported_back() -> None:
    model = ScriptedModel([[ToolCall("c1", "read_page", "{not json")], ["好的"]])
    await collect(model, offline_toolbox())
    assert model.calls[1][0][-1]["content"] == "工具参数格式不对，请重新调用。"


@pytest.mark.anyio
async def test_read_failure_is_reported_back() -> None:
    model = ScriptedModel([[ToolCall("c1", "read_page", '{"url": "https://example.com/missing"}')], ["好的"]])
    events = await collect(model, offline_toolbox())

    assert {"type": "status", "text": "正在阅读 example.com"} in events
    assert model.calls[1][0][-1]["content"] == "读取失败：网页打不开（HTTP 404）"


@pytest.mark.anyio
async def test_search_tool_hidden_when_not_configured() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(404)))
    toolbox = web_toolbox(client, search=None)
    model = ScriptedModel([["好的"]])
    await collect(model, toolbox)

    tools = model.calls[0][1]
    assert tools is not None
    assert [t["function"]["name"] for t in tools] == ["read_page"]


def test_mock_model_searches_end_to_end() -> None:
    app.dependency_overrides[get_model] = lambda: MockModel(delay=0)
    app.dependency_overrides[get_registry] = offline_registry
    try:
        res = TestClient(app).post("/chat", json={"messages": [{"role": "user", "content": "帮我搜一下 Python 3.13 新特性"}]})
    finally:
        app.dependency_overrides.clear()
    events = parse_sse(res.text)
    text = "".join(e.get("text", "") for e in events if e["type"] == "text")

    assert {"type": "status", "text": "正在搜索「Python 3.13 新特性」"} in events
    assert "查到了" in text
    assert events[-1] == {"type": "done"}
