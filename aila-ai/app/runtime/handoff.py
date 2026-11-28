"""换别家的模型接着做：给接手的模型写一份前情（runner.handoff 用）。

任务做到一半，你在输入框右下角选了另一家的模型（比如从 GPT 换成 Claude）：它们的对话记录互相不通，
所以开一个新任务接着做，把前情交给新的模型。前情由代码从日志里整理，不让大模型自己概括：
你说过的要求、它最后说的话、改过的文件、跑过的命令、最后一轮怎么结束的。文件内容不抄，以项目里现在的为准。
"""

from __future__ import annotations

from typing import Any

MAX_ASKS = 6
MAX_REPLIES = 3
MAX_FILES = 40
MAX_COMMANDS = 6
ASK_CHARS = 600
REPLY_CHARS = 900

REASONS = {
    "done": "做完了", "interrupted": "被你打断了", "rejected": "你没同意它要做的事", "denied": "按权限规则没让它做",
    "timeout": "等你确认等超时了", "max_steps": "步数到上限了", "budget": "花费到上限了", "error": "出错了",
}


def recap(events: list[dict[str, Any]], previous: str) -> str:
    """给接手的模型的前情（previous：之前是谁在做，比如「Codex（gpt-6.2）」）"""
    asks = [_cut(str(e.get("text", "")), ASK_CHARS) for e in events if e["type"] == "user" and str(e.get("text", "")).strip()]
    replies = [_cut(str(e.get("text", "")), REPLY_CHARS) for e in events
               if e["type"] == "assistant" and str(e.get("text", "")).strip()]
    files: list[str] = []
    for e in events:
        if e["type"] == "tool" and e.get("status") == "ok" and e.get("file") and e["file"] not in files:
            files.append(str(e["file"]))
    commands = [str(e["command"])[:200] for e in events if e["type"] == "tool_start" and e.get("command")]
    if not commands:
        commands = [_command_of(e) for e in events if e["type"] == "tool" and e.get("name") == "run_command" and _command_of(e)]
    last = next((e for e in reversed(events) if e["type"] == "turn_end"), None)
    lines = [f"（接手说明：这个任务之前是 {previous} 在做，现在换你接着做。下面是艾拉从记录里整理的前情；"
             "文件以项目里现在的内容为准，需要的话自己看。）"]
    if asks:
        lines.append("用户提过的要求（从早到晚）：")
        lines += [f"- {a}" for a in asks[-MAX_ASKS:]]
    if replies:
        lines.append(f"{previous} 最后说的：")
        lines += [r for r in replies[-MAX_REPLIES:]]
    if files:
        shown = files[:MAX_FILES]
        lines.append("改过的文件：" + "、".join(shown) + (f" 等 {len(files)} 个" if len(files) > len(shown) else ""))
    if commands:
        lines.append("跑过的命令（最近几条）：" + "；".join(commands[-MAX_COMMANDS:]))
    if last is not None:
        why = REASONS.get(str(last.get("reason")), str(last.get("reason", "")))
        lines.append(f"最后一轮：{why}" + (f"（{_cut(str(last['error']), 200)}）" if last.get("error") else ""))
    lines.append("（接手说明完）")
    return "\n".join(lines)


def _command_of(event: dict[str, Any]) -> str:
    args = event.get("args")
    return str(args.get("command", ""))[:200] if isinstance(args, dict) else ""


def _cut(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


__all__ = ["recap"]
