"""子 Agent（explore 工具）：只读、用快模型、报告回到主会话、几个一起跑、用量单独记"""

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from app.llm import Message, StepItem, ToolCall, Usage
from app.runtime.mock import EXPLORE_DEMO, MockCoderModel
from app.runtime.runner import Runtime
from app.runtime.shell import find_shell
from app.runtime.subagent import EXPLORE_PROMPT, READ_TOOLS, REPORT_CHARS

from .test_runtime import Scripted

SHELL = find_shell()


def explore(cid: str, task: str) -> ToolCall:
    return ToolCall(cid, "explore", json.dumps({"task": task}, ensure_ascii=False))


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "login.py").write_text("def login(user):\n    raise ValueError('密码不对')\n", encoding="utf-8")
    (root / ".env").write_text("TOKEN=abc\n", encoding="utf-8")
    return root


class Slow:
    """查资料的假快模型：每个子 Agent 一步就交报告；记下最多有几个同时在跑"""

    name = "fast"
    active = 0
    peak = 0

    async def step(self, messages: list[Message], tools: list[dict[str, Any]] | None, max_tokens: int = 8192) -> AsyncIterator[StepItem]:
        Slow.active += 1
        Slow.peak = max(Slow.peak, Slow.active)
        await asyncio.sleep(0.1)
        Slow.active -= 1
        task = str(messages[-1]["content"]).rsplit("\n", 1)[-1]
        yield f"报告：{task}"
        yield Usage(prompt=1000, completion=50, cache_hit=800, cache_miss=200)


@pytest.mark.anyio
async def test_explore_reports_back_with_read_only_tools(project: Path, tmp_path: Path) -> None:
    main = Scripted([[explore("e1", "登录失败的报错在哪里抛出")], ["在 src/login.py:2。"]])
    children: list[Scripted] = []

    def fast() -> Scripted:
        child = Scripted([
            [ToolCall("r1", "read_file", json.dumps({"path": ".env"}))],  # 密钥文件：要问，但子 Agent 没法问 → 不读
            [ToolCall("r2", "search", json.dumps({"pattern": "密码不对"}))],
            ["登录失败在 src/login.py:2 抛出 ValueError。", Usage(prompt=900, completion=30, cache_hit=0, cache_miss=900)],
        ])
        child.name = "fast"  # type: ignore[misc]
        children.append(child)
        return child

    rt = Runtime(tmp_path / "data", lambda: main, shell=SHELL, fast_factory=fast)
    sid = rt.create(str(project), mode="auto").id
    events = [e async for e in rt.run(sid, "登录报错是哪来的")]
    assert events[-1]["type"] == "turn_end" and events[-1]["reason"] == "done"

    # 子 Agent：自己的系统提示词、只有读的工具、同一份环境说明
    child = children[0]
    messages, tools = child.requests[0]
    assert messages[0]["content"] == EXPLORE_PROMPT
    assert {t["function"]["name"] for t in tools or []} <= set(READ_TOOLS)
    session = rt.agent(sid).session
    assert messages[1]["content"].startswith(session.context)

    tool_events = [e for e in session.events if e["type"] == "tool"]
    assert tool_events[0]["name"] == "explore" and tool_events[0]["status"] == "ok"
    assert tool_events[0]["content"] == "登录失败在 src/login.py:2 抛出 ValueError。"
    assert tool_events[0]["model"] == "fast" and tool_events[0]["steps"] == 3
    # 主会话里只有报告，没有子 Agent 读过的内容
    assert all("TOKEN" not in json.dumps(e, ensure_ascii=False) for e in session.events)

    # 子会话有自己的日志；读 .env 被拒了
    sub = session.path.with_suffix(".sub") / f"{tool_events[0]['sub']}.jsonl"
    sub_events = [json.loads(line) for line in sub.read_text(encoding="utf-8").splitlines()]
    assert sub_events[0]["parent"] == sid and sub_events[0]["mode"] == "read_only"
    assert [e["status"] for e in sub_events if e["type"] == "tool"] == ["denied", "ok"]

    # 用量记在主会话里，标成 explore；不影响主 Agent 要不要压缩的判断
    usage = [e for e in session.events if e["type"] == "usage"]
    assert [u.get("purpose") for u in usage] == ["explore"]
    assert usage[0]["prompt"] == 900 and usage[0]["model"] == "fast"
    assert session.last_prompt_tokens == 0
    # 会话列表里不出现子会话
    assert [s["id"] for s in rt.store.list()] == [sid]


@pytest.mark.anyio
async def test_several_explores_in_one_step_run_together(project: Path, tmp_path: Path) -> None:
    Slow.active = Slow.peak = 0
    main = Scripted([[explore("a", "问题 A"), explore("b", "问题 B"), explore("c", "问题 C")], ["都查完了。"]])
    rt = Runtime(tmp_path / "data", lambda: main, shell=SHELL, fast_factory=Slow)
    sid = rt.create(str(project)).id
    events = [e async for e in rt.run(sid, "查三件事")]
    assert Slow.peak == 3  # 三个一起跑
    tools = [e for e in events if e["type"] == "tool"]
    assert [e["id"] for e in tools] == ["a", "b", "c"]  # 结果按调用的顺序记
    assert [e["content"] for e in tools] == ["报告：问题 A", "报告：问题 B", "报告：问题 C"]
    # 下一步发给主模型的消息：三个结果紧跟着那条 assistant，顺序不变
    messages, _ = main.requests[1]
    assert [m.get("tool_call_id") for m in messages[-3:]] == ["a", "b", "c"]
    session = rt.agent(sid).session
    assert sum(1 for e in session.events if e.get("purpose") == "explore") == 3
    assert session.usage.prompt == 3000


@pytest.mark.anyio
async def test_interrupt_stops_running_explores(project: Path, tmp_path: Path) -> None:
    main = Scripted([[explore("a", "问题 A"), explore("b", "问题 B")], ["不会走到这里"]])

    class Stuck(Slow):
        async def step(self, messages: list[Message], tools: list[dict[str, Any]] | None, max_tokens: int = 8192) -> AsyncIterator[StepItem]:
            await asyncio.sleep(30)
            yield "太慢了"

    rt = Runtime(tmp_path / "data", lambda: main, shell=SHELL, fast_factory=Stuck)
    sid = rt.create(str(project)).id
    seen = []
    async for e in rt.run(sid, "查两件事"):
        seen.append(e)
        if e["type"] == "tool_start" and e["id"] == "b":
            await asyncio.sleep(0.05)
            assert rt.interrupt(sid)
    session = rt.agent(sid).session
    assert [e["status"] for e in session.events if e["type"] == "tool"] == ["interrupted", "interrupted"]
    assert session.events[-1]["reason"] == "interrupted"
    # 子会话也记成被打断
    subs = list(session.path.with_suffix(".sub").glob("*.jsonl"))
    assert len(subs) == 2
    for f in subs:
        last = json.loads(f.read_text(encoding="utf-8").splitlines()[-1])
        assert last["type"] == "turn_end" and last["reason"] == "interrupted"


@pytest.mark.anyio
async def test_empty_task_and_long_report(project: Path, tmp_path: Path) -> None:
    long = "很长" * REPORT_CHARS
    main = Scripted([[explore("a", "  "), explore("b", "写长报告")], ["好"]])
    rt = Runtime(tmp_path / "data", lambda: main, shell=SHELL, fast_factory=lambda: Scripted([[long]]))
    sid = rt.create(str(project)).id
    events = [e async for e in rt.run(sid, "查")]
    a, b = [e for e in events if e["type"] == "tool"]
    assert a["status"] == "error" and "task 是空的" in a["content"]
    assert len(b["content"]) < REPORT_CHARS + 50 and b["content"].endswith("后面省略了）")


@pytest.mark.anyio
async def test_mock_explore_demo(project: Path, tmp_path: Path) -> None:
    """没填密钥时说「演示查资料」：派两个助手一起查，各自交回报告"""
    (project / "README.md").write_text("# 登录服务\n", encoding="utf-8")
    rt = Runtime(tmp_path / "data", lambda: MockCoderModel(delay=0), shell=SHELL,
                 fast_factory=lambda: MockCoderModel(delay=0))
    sid = rt.create(str(project)).id
    events = [e async for e in rt.run(sid, EXPLORE_DEMO)]
    tools = [e for e in events if e["type"] == "tool"]
    assert [t["name"] for t in tools] == ["explore", "explore"]
    assert "登录服务" in tools[0]["content"]
    assert events[-1]["reason"] == "done"
