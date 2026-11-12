"""对话主循环：大模型说话 → 需要的话调用工具 → 把结果给它 → 继续说，直到给出最终回答。

每一轮的文字都边收边解析标签、边推给桌面端，所以她会先说「我查一下」，
然后气泡里显示「正在搜索……」，查完再接着说结论。
"""

from collections.abc import AsyncIterator
from typing import Any

from .llm import ChatModel, Message, ToolCall
from .tags import Event, TagParser
from .tools import Toolbox

# 最多调用几轮工具；最后一轮不再提供工具，逼它根据已有信息直接回答
MAX_TOOL_ROUNDS = 3


async def run_agent(model: ChatModel, messages: list[Message], toolbox: Toolbox) -> AsyncIterator[Event]:
    convo = list(messages)
    parser = TagParser()

    for round_no in range(MAX_TOOL_ROUNDS + 1):
        tools = toolbox.specs if round_no < MAX_TOOL_ROUNDS else None
        text = ""
        calls: list[ToolCall] = []

        async for item in model.stream(convo, tools):
            if isinstance(item, ToolCall):
                calls.append(item)
            else:
                text += item
                for event in parser.feed(item):
                    yield event
        for event in parser.flush():
            yield event

        if not calls or tools is None:
            return  # 最后一轮没给工具，个别模型还是会要，不再执行

        convo.append(_assistant_tool_message(text, calls))
        for call in calls:
            yield {"type": "status", "text": toolbox.status(call)}
            convo.append({"role": "tool", "tool_call_id": call.id, "content": await toolbox.run(call)})


def _assistant_tool_message(text: str, calls: list[ToolCall]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": text,
        "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}} for c in calls
        ],
    }
