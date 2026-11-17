"""没填大模型密钥时的假编程模型：固定走一遍「列文件 → 读 README → 总结」，让整条链路（工具、权限、日志、
接口、界面）不联网也能跑通。用量按前缀估算（见 cache.py），可以看出上下文排布对缓存是否友好。

长任务也能演示：让它「出方案」，它看一眼文件列表就交一份两个阶段的计划；按计划做时每个阶段读一下 README 就标完成。"""

import asyncio
import json
import re
import secrets
from collections.abc import AsyncIterator
from typing import Any

from ..llm import Message, StepItem, ToolCall
from .cache import PrefixMeter

NOTE = "（我现在是假模型：在设置里填好大模型密钥，才能真的帮你读代码、改代码。）"
# 对假模型说这句话：走一遍改文件的完整流程（待办、读、改之前问你、跑命令），不用密钥也能看到工作窗口的每种卡片
DEMO = "演示改文件"
# 「演示查资料」：派两个子 Agent 一起去查（explore 工具），看子 Agent 的卡片和用量
EXPLORE_DEMO = "演示查资料"
# 出方案、按计划做的那几句话由代码生成（planning.py），认开头
PLAN_MARK = re.compile(r"^先为下面这个目标出一份分阶段的计划", re.MULTILINE)
STAGE_MARK = re.compile(r"^(开始第 \d+/\d+ 阶段|第 \d+/\d+ 阶段「)", re.MULTILINE)
GOAL = re.compile(r"^目标：(.+)$", re.MULTILINE)


class MockCoderModel:
    name = "mock-coder"

    def __init__(self, delay: float = 0.02) -> None:
        self.delay = delay
        self.meter = PrefixMeter()
        self._n = 0

    async def step(self, messages: list[Message], tools: list[dict[str, Any]] | None, max_tokens: int = 8192) -> AsyncIterator[StepItem]:
        last = messages[-1]
        call: ToolCall | None = None
        user = _last_user(messages)
        if PLAN_MARK.search(user):
            text, call = self._plan(messages, user)
        elif STAGE_MARK.search(user):
            text, call = self._stage(messages)
        elif DEMO in user and not (last["role"] == "user" and "交接说明" in str(last["content"])):
            text, call = self._demo(messages)
        elif EXPLORE_DEMO in _last_user(messages):
            text, calls = self._explore_demo(messages)
            for i in range(0, len(text), 4):
                yield text[i : i + 4]
            for c in calls:
                yield c
            yield self.meter.measure(messages, tools, len(text) + sum(len(c.arguments) for c in calls))
            return
        elif last["role"] == "user" and "交接说明" in str(last["content"]):
            text = "任务：用户让我看看这个项目。已经列过文件、读过 README。还没有改任何文件。"
        elif last["role"] == "user":
            text = "我先看看项目里有哪些文件。"
            call = self._call("list_files", {"pattern": "*"})
        else:
            previous = _last_tool_name(messages)
            content = str(last["content"])
            readme = re.search(r"^(\S*README\.md)$", content, re.MULTILINE | re.IGNORECASE)
            if previous == "list_files" and readme:
                text = "有 README，我读一下开头。"
                call = self._call("read_file", {"path": readme.group(1), "limit": 30})
            elif previous == "read_file":
                first = next((line.split("\t", 1)[-1] for line in content.splitlines() if line.split("\t", 1)[-1].strip()), "")
                text = f"看完了。README 第一行是「{first.strip()[:60]}」。{NOTE}"
            else:
                text = f"看过了。{NOTE}"
        for i in range(0, len(text), 4):
            if self.delay:
                await asyncio.sleep(self.delay)
            yield text[i : i + 4]
        if call:
            yield call
        yield self.meter.measure(messages, tools, len(text) + (len(call.arguments) if call else 0))

    def _demo(self, messages: list[Message]) -> tuple[str, ToolCall | None]:
        """「演示改文件」：列待办 → 读 README → 改 README（会问你）→ 看 git diff → 收尾。界面上每种卡片都能看到"""
        previous = _last_tool_name(messages) if messages[-1]["role"] == "tool" else ""
        content = str(messages[-1]["content"])
        steps = ["读 README", "在 README 里加一行", "看看改动"]
        todo = lambda done: [{"content": s, "status": "completed" if i < done else "in_progress" if i == done else "pending"}  # noqa: E731
                             for i, s in enumerate(steps)]
        if not previous:
            return "好，我演示一遍改文件的过程。", self._call("todo_write", {"items": todo(0)})
        if previous == "todo_write" and "[x] 看看改动" in content:
            return f"演示完了：在 README 里加了一行，你可以直接删掉它。{NOTE}", None
        if previous == "todo_write":
            return "", self._call("read_file", {"path": "README.md", "limit": 20})
        if previous == "read_file":
            first = next((line.split("\t", 1)[-1] for line in content.splitlines() if "\t" in line), "")
            if not first.strip():
                return "这个项目没有 README，演示就到这里。", None
            return "在第一行后面加一句说明。", self._call(
                "edit_file", {"path": "README.md", "old_string": first, "new_string": first + "\n\n（这一行是艾拉演示改文件时加的，可以删掉）"})
        if previous == "edit_file":
            if not content.startswith("改好了"):
                return "没改成，演示就到这里。", None
            return "", self._call("run_command", {"command": "git diff --stat", "description": "看看改了哪些文件"})
        if previous == "run_command":
            return "", self._call("todo_write", {"items": todo(3)})
        return f"演示结束。{NOTE}", None

    def _explore_demo(self, messages: list[Message]) -> tuple[str, list[ToolCall]]:
        """「演示查资料」：同一步里派两个助手去查（一起跑），拿回报告再总结"""
        if messages[-1]["role"] != "tool":
            return "我派两个助手分头去查。", [
                self._call("explore", {"task": "这个项目是做什么的？看看 README。"}),
                self._call("explore", {"task": "项目里有哪些文件？"}),
            ]
        return f"两个助手都查完了，报告在上面的卡片里。{NOTE}", []

    def _plan(self, messages: list[Message], user: str) -> tuple[str, ToolCall | None]:
        """出方案：先看文件列表，再交一份两个阶段的计划"""
        if messages[-1]["role"] == "user":
            return "我先看看项目里有哪些文件。", self._call("list_files", {"pattern": "*"})
        previous = _last_tool_name(messages)
        if previous == "list_files":
            goal = (GOAL.search(user).group(1).strip() if GOAL.search(user) else "这个任务")[:80]
            return "看过了，分两步做。", self._call("propose_plan", {"goal": goal, "stages": [
                {"title": "读一遍项目", "goal": "弄清相关代码在哪", "verify": "能说出要改哪些文件"},
                {"title": "动手并验证", "goal": f"完成：{goal}", "verify": "跑项目的测试"},
            ]})
        return f"计划交给你了：先读一遍项目，再动手并验证。{NOTE}", None

    def _stage(self, messages: list[Message]) -> tuple[str, ToolCall | None]:
        """按计划做的一个阶段：读一下 README，再标完成"""
        if messages[-1]["role"] == "user":
            return "开始这一段。", self._call("read_file", {"path": "README.md", "limit": 10})
        previous = _last_tool_name(messages)
        if previous == "read_file":
            return "", self._call("stage_done", {"summary": "（假模型）读了 README，没有改文件", "verified": "假模型不跑测试"})
        return "这一段做完了。", None

    def _call(self, name: str, args: dict[str, Any]) -> ToolCall:
        self._n += 1
        return ToolCall(f"mock-{secrets.token_hex(4)}-{self._n}", name, json.dumps(args, ensure_ascii=False))


def _last_user(messages: list[Message]) -> str:
    for m in reversed(messages):
        if m["role"] == "user":
            return str(m["content"])
    return ""


def _last_tool_name(messages: list[Message]) -> str:
    for m in reversed(messages):
        if m["role"] == "assistant" and m.get("tool_calls"):
            return str(m["tool_calls"][-1]["function"]["name"])
    return ""
