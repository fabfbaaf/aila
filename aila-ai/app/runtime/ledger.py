"""上下文账本：下一步发给模型的上下文由哪几块组成、各占多少、离压缩线还有多远。

- 按发送顺序分块：系统提示词、工具列表、会话开头的环境说明（AGENTS.md 等）、交接说明（压缩过的话）、
  你说的话、她说的话和工具调用、工具结果（按工具分开算）
- 大小先按字数估算，再按最近一步服务端返回的真实输入 token 数等比例校准（没有用量时只是估算）
- 开头那几块（系统提示词、工具列表、环境说明）每一步都一样：就是前缀缓存命中的部分。
  所以这张账本也能看出「按缓存排上下文」的效果：固定的在前，越往后越新
- 列出最大的几个工具结果（读的哪个文件、哪条命令的输出），知道上下文是被谁占满的
"""

import json
from typing import Any

from .cache import CHARS_PER_TOKEN
from .loop import Agent

LABELS = {
    "system": "系统提示词",
    "tools": "工具列表",
    "context": "环境说明（AGENTS.md 等）",
    "summary": "交接说明（压缩过）",
    "user": "你说的话",
    "assistant": "她说的话和工具调用",
}
TOOL_LABELS = {
    "read_file": "读文件",
    "list_files": "找文件",
    "search": "搜内容",
    "edit_file": "改文件",
    "write_file": "写文件",
    "run_command": "命令输出",
    "todo_write": "待办清单",
    "code_search": "按意思搜",
    "code_locate": "按报错定位",
    "code_symbol": "调用关系",
    "explore": "助手的报告",
    "job_output": "后台任务的输出",
    "job_stop": "后台任务的输出",
}
TOP = 5


def context_ledger(agent: Agent) -> dict[str, Any]:
    session = agent.session
    messages = session.messages(agent.system)
    tools_json = json.dumps(agent.tools.specs, ensure_ascii=False)
    chars: dict[str, int] = {"system": len(agent.system), "tools": len(tools_json)}
    order = ["system", "tools"]
    calls: dict[str, tuple[str, dict[str, Any]]] = {}
    items: list[tuple[str, int]] = []

    def add(key: str, n: int) -> None:
        if key not in chars:
            chars[key] = 0
            order.append(key)
        chars[key] += n

    context = session.context
    for i, m in enumerate(messages[1:], 1):
        content = str(m.get("content") or "")
        if m["role"] == "user":
            if i == 1 and context and content.startswith(context):
                add("context", len(context))
                content = content[len(context) :]
            if "<之前的进展>" in content:
                add("summary", len(content))
            elif content.strip():
                add("user", len(content))
        elif m["role"] == "assistant":
            size = len(content)
            for c in m.get("tool_calls") or []:
                fn = c["function"]
                size += len(fn["name"]) + len(fn["arguments"])
                try:
                    args = json.loads(fn["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                calls[c["id"]] = (fn["name"], args if isinstance(args, dict) else {})
            add("assistant", size)
        elif m["role"] == "tool":
            name, args = calls.get(m.get("tool_call_id", ""), ("", {}))
            add(f"tool:{name}", len(content))
            items.append((_what(name, args), len(content)))

    total_chars = sum(chars.values()) or 1
    last = _last_usage(session.events)
    estimated = not last
    prompt = last["prompt"] if last else -(-total_chars // CHARS_PER_TOKEN)
    scale = prompt / total_chars

    def tokens(n: int) -> int:
        return round(n * scale)

    segments = [
        {"key": key, "label": _label(key), "tokens": tokens(chars[key]), "fixed": key in ("system", "tools", "context")}
        for key in order
        if chars[key]
    ]
    biggest = sorted(items, key=lambda x: -x[1])[:TOP]
    config = agent.config
    return {
        "window": config.context_window,
        "compact_at": config.compact_at,
        "prompt": prompt,
        "cache_hit": last["cache_hit"] if last else 0,
        "estimated": estimated,
        "segments": segments,
        "biggest": [{"label": label, "tokens": tokens(n)} for label, n in biggest if n],
        "messages": len(messages),
    }


def _label(key: str) -> str:
    if key.startswith("tool:"):
        name = key[5:]
        return f"工具结果：{TOOL_LABELS.get(name, name or '其他')}"
    return LABELS.get(key, key)


def _what(name: str, args: dict[str, Any]) -> str:
    """一个工具结果是什么：读的哪个文件、哪条命令……"""
    if name in ("read_file", "edit_file", "write_file"):
        return f"{TOOL_LABELS[name]} {args.get('path', '')}"
    if name == "run_command":
        return f"命令 {str(args.get('command', ''))[:40]}"
    if name in ("search", "code_search"):
        return f"{TOOL_LABELS[name]}「{str(args.get('pattern') or args.get('query') or '')[:30]}」"
    return TOOL_LABELS.get(name, name)


def _last_usage(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """最近一步的用量（压缩以后还没请求过的话就没有：之前的不准了）"""
    for e in reversed(events):
        if e["type"] == "compaction":
            return None
        if e["type"] == "usage" and not e.get("purpose"):  # 压缩、子 Agent 的不算
            return e
    return None
