"""对话主循环：大模型说话 → 需要的话调用工具 → 把结果给它 → 继续说，直到给出最终回答。

每一轮的文字都边收边解析标签、边推给桌面端，所以她会先说「我查一下」，
然后气泡里显示「正在搜索……」，查完再接着说结论。

会改东西的工具（打开网页、发消息……）执行前先推送 confirm 事件，等用户回答：
同意了才执行，不同意或者一直没回答就告诉大模型「没有执行」。
"""

from collections.abc import AsyncIterator
from typing import Any

from .confirm import Confirmations, Result
from .llm import ChatModel, Message, ToolCall
from .tags import Event, TagParser
from .tools import EMIT, Toolbox

# 最多调用几轮工具；最后一轮不再提供工具，逼它根据已有信息直接回答。
# 网页操作一步一轮（打开 → 搜索 → 点结果 → 看内容），所以给得比较宽
MAX_TOOL_ROUNDS = 8
# 网页快照这类「现在的样子」：有了新的，旧的换成这句话，对话不会越来越长
STALE_NOTE = "（这是之前的页面，已经省略；以最新的为准）"

# 没执行时交给大模型的话：明确告诉它别换个办法绕过去
NOT_DONE: dict[Result, str] = {
    "rejected": "用户没有同意，这个操作没有执行。不要再试，也不要换别的工具或办法去做；简单回应一句就好。",
    "timeout": "用户一直没有回应，这个操作没有执行。不要再试；简单说一句就好。",
}


async def run_agent(
    model: ChatModel, messages: list[Message], toolbox: Toolbox, confirms: Confirmations | None = None
) -> AsyncIterator[Event]:
    """confirms 为 None 时没法问用户，要确认的工具一律不执行"""
    convo = list(messages)
    parser = TagParser()
    snapshots: list[int] = []  # convo 里网页快照的位置

    def add_result(call: ToolCall, content: str) -> None:
        # 出错时返回的是一句话，不是快照，不能把之前的页面挤掉（快照里都有「网页标题：」这一行）
        if toolbox.is_ephemeral(call.name) and "网页标题：" in content:
            for i in snapshots:
                convo[i] = {**convo[i], "content": STALE_NOTE}
            snapshots.clear()
            snapshots.append(len(convo))
        convo.append({"role": "tool", "tool_call_id": call.id, "content": content})

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
            approved = False
            ask = toolbox.confirmation(call) if confirms is not None else None
            if ask is not None and confirms is not None:
                pending = confirms.open()
                try:
                    yield {"type": "confirm", "id": pending.id, "skill": ask.skill, "action": ask.action, "detail": ask.detail}
                    result = await confirms.wait(pending)
                finally:
                    confirms.close(pending)  # 回复被取消时也要清掉
                yield {"type": "confirm_end", "id": pending.id, "result": result}
                if result != "approved":
                    convo.append({"role": "tool", "tool_call_id": call.id, "content": NOT_DONE[result]})
                    continue
                approved = True
            yield {"type": "status", "text": toolbox.status(call)}
            side: list[dict[str, Any]] = []
            token = EMIT.set(side.append)
            try:
                result = await toolbox.run(call, approved)
            finally:
                EMIT.reset(token)
            add_result(call, result)
            for event in side:  # 工具想告诉桌面端的事（比如打开工作窗口）
                yield event  # type: ignore[misc]


def _assistant_tool_message(text: str, calls: list[ToolCall]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": text,
        "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}} for c in calls
        ],
    }
