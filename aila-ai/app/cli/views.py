"""终端里的执行轨迹（/trace）和上下文账本（/context）。

执行轨迹和工作窗口的 trace.ts 是同一个算法：一轮里每次模型请求、每个工具调用、每次等你确认各一行，
时间来自会话日志（事件的 t、assistant 的 timing、tool 的 seconds），用方块字符画成横条。
"""

import json
from dataclasses import dataclass, field
from typing import Any

from rich.console import Group
from rich.text import Text

Event = dict[str, Any]
BAR = 36  # 时间轴有多宽（字符）
LEDGER_BAR = 48

ACTIONS = {
    "read_file": lambda a: f"读 {a.get('path', '')}",
    "list_files": lambda a: f"找文件 {a.get('pattern') or '*'}",
    "search": lambda a: f"搜「{str(a.get('pattern', ''))[:30]}」",
    "edit_file": lambda a: f"改 {a.get('path', '')}",
    "write_file": lambda a: f"写 {a.get('path', '')}",
    "run_command": lambda a: ("后台：" if a.get("background") else "") + str(a.get("description") or a.get("command", ""))[:50],
    "todo_write": lambda a: "更新待办清单",
    "code_search": lambda a: f"按意思搜「{str(a.get('query', ''))[:24]}」",
    "code_locate": lambda a: "按报错定位代码",
    "code_symbol": lambda a: f"看 {str(a.get('name', ''))[:30]} 的调用关系",
    "explore": lambda a: f"派助手去查「{str(a.get('task', '')).strip()[:24]}」",
    "job_output": lambda a: f"看后台任务 {a.get('id', '')} 的输出",
    "job_stop": lambda a: f"停掉后台任务 {a.get('id', '')}",
}


def action(name: str, args: dict[str, Any]) -> str:
    fn = ACTIONS.get(name)
    return fn(args) if fn else name


@dataclass
class Row:
    kind: str  # model / tool / wait
    label: str
    start: float
    end: float
    status: str = ""
    first: float | None = None
    prompt: int = 0
    hit: int = 0


@dataclass
class Turn:
    text: str
    rows: list[Row] = field(default_factory=list)
    duration: float = 0.0
    reason: str = ""
    steps: int = 0
    prompt: int = 0
    hit: int = 0
    completion: int = 0


def _args(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def build_trace(events: list[Event]) -> list[Turn]:
    turns: list[Turn] = []
    turn: Turn | None = None
    origin = cursor = 0.0
    last_model: Row | None = None
    calls: dict[str, tuple[str, dict[str, Any]]] = {}
    confirms: dict[str, tuple[float, str]] = {}
    for e in events:
        t = e.get("t")
        if not isinstance(t, (int, float)):
            continue
        kind = e.get("type")
        if kind == "user":
            origin = cursor = float(t)
            turn = Turn(str(e.get("text", "")))
            turns.append(turn)
            continue
        if turn is None:
            continue

        def rel(x: float) -> float:
            return max(0.0, x - origin)

        if kind == "assistant":
            timing = e.get("timing") or {}
            start = max(cursor, t - timing["total"]) if "total" in timing else cursor
            tc = e.get("tool_calls") or []
            for c in tc:
                calls[c["id"]] = (c["name"], _args(c.get("arguments")))
            label = "想下一步 → " + "、".join(action(c["name"], _args(c.get("arguments"))) for c in tc) if tc else "回答"
            last_model = Row("model", label, rel(start), rel(t), first=timing.get("first"))
            turn.rows.append(last_model)
            turn.steps += 1
            cursor = t
        elif kind == "usage" and e.get("purpose") != "compaction":
            if last_model and not e.get("purpose"):  # 子 Agent 的用量只算进这一轮的总数
                last_model.prompt, last_model.hit = int(e.get("prompt", 0)), int(e.get("cache_hit", 0))
            turn.prompt += int(e.get("prompt", 0))
            turn.hit += int(e.get("cache_hit", 0))
            turn.completion += int(e.get("completion", 0))
        elif kind == "confirm":
            confirms[str(e.get("id"))] = (t, f"等你确认：{e.get('action', '')}")
            cursor = t
        elif kind == "confirm_end":
            if str(e.get("id")) in confirms:
                start, label = confirms[str(e.get("id"))]
                turn.rows.append(Row("wait", label, rel(start), rel(t), str(e.get("result", ""))))
            cursor = t
        elif kind == "tool":
            name, args = calls.get(str(e.get("id")), (str(e.get("name", "")), {}))
            seconds = e.get("seconds")
            start = max(cursor, t - seconds) if isinstance(seconds, (int, float)) else cursor
            turn.rows.append(Row("tool", action(name, args), rel(start), rel(t), str(e.get("status", "ok"))))
            cursor = t
        elif kind == "compaction":
            turn.rows.append(Row("model", "压缩上下文", rel(cursor), rel(t)))
            cursor = t
        elif kind == "turn_end":
            turn.reason = str(e.get("reason", ""))
        turn.duration = max(turn.duration, rel(t))
    return turns


def _k(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def _secs(x: float) -> str:
    return f"{x:.0f}s" if x >= 10 else f"{x:.1f}s"


def trace_view(turn: Turn) -> Group:
    """一轮的时间线：每行 标签 | 横条 | 用时"""
    lines: list[Text] = []
    head = Text.assemble(("轨迹 ", "bold"), (turn.text.splitlines()[0][:50] if turn.text.strip() else "", "bold"))
    stats = f"  {_secs(turn.duration)} · {turn.steps} 步"
    if turn.prompt:
        stats += f" · 输入 {_k(turn.prompt)}（命中缓存 {turn.hit / turn.prompt:.0%}）· 输出 {_k(turn.completion)}"
    lines.append(head + Text(stats, style="dim"))
    total = max(turn.duration, 0.001)
    for r in turn.rows:
        a = min(BAR - 1, int(r.start / total * BAR))
        b = max(a + 1, min(BAR, round(r.end / total * BAR)))
        style = {"model": "magenta", "wait": "bright_black"}.get(r.kind) or (
            "cyan" if r.status == "ok" else "red" if r.status in ("error", "denied") else "yellow")
        char = "░" if r.kind == "wait" else "█"
        bar = Text(" " * a) + Text(char * (b - a), style=style) + Text(" " * (BAR - b))
        label = r.label if len(r.label) <= 24 else r.label[:23] + "…"
        extra = f" 命中{r.hit / r.prompt:.0%}" if r.prompt else ""
        lines.append(Text(f"  {label:<24} ", style="dim" if r.kind == "wait" else "") + bar + Text(f" {_secs(r.end - r.start)}{extra}", style="dim"))
    if len(turn.rows) > 2:
        slow = sorted(turn.rows, key=lambda r: r.start - r.end)[:3]
        lines.append(Text("  最慢：" + "、".join(f"{r.label} {_secs(r.end - r.start)}" for r in slow), style="dim"))
    lines.append(Text("  █ 她在想  █ 工具  ░ 等你确认", style="dim"))
    return Group(*lines)


LEDGER_STYLES = {"system": "cyan", "tools": "bright_cyan", "context": "turquoise2", "summary": "medium_purple1",
                 "user": "grey70", "assistant": "grey50"}
TOOL_STYLES = ["yellow", "dark_orange", "gold1", "orange3"]


def ledger_view(ledger: dict[str, Any]) -> Group:
    """上下文账本：一条横条代表整个窗口，开头固定的几块命中缓存；下面列出各块和最占地方的"""
    window = max(int(ledger["window"]), 1)
    bar = Text()
    legend: list[Text] = []
    used = 0
    tool_i = 0
    for s in ledger["segments"]:
        style = LEDGER_STYLES.get(s["key"])
        if style is None:
            style = TOOL_STYLES[tool_i % len(TOOL_STYLES)]
            tool_i += 1
        width = max(1, round(s["tokens"] / window * LEDGER_BAR)) if s["tokens"] else 0
        bar.append("█" * width, style=style)
        used += width
        legend.append(Text.assemble(("  ■ ", style), f"{s['label']:<16}", (f"{_k(s['tokens']):>7}", "dim"),
                                    ("  （固定，命中缓存）" if s["fixed"] else "", "dim")))
    line = int(ledger["compact_at"] * LEDGER_BAR)
    rest = max(0, LEDGER_BAR - used)
    tail = Text("·" * rest, style="bright_black")
    if used <= line < LEDGER_BAR:
        tail = Text("·" * (line - used), style="bright_black") + Text("|", style="yellow") + Text("·" * max(0, rest - (line - used) - 1), style="bright_black")
    pct = ledger["prompt"] / window
    head = Text.assemble(("上下文账本 ", "bold"), (f"下一步输入{'约' if ledger['estimated'] else ''} {_k(ledger['prompt'])} / {_k(window)}（{pct:.0%}）", ""))
    hit = ledger["cache_hit"] / ledger["prompt"] if ledger["prompt"] and not ledger["estimated"] else None
    sub = Text(f"  黄线是压缩线（{ledger['compact_at']:.0%}）" + (f" · 上一步命中缓存 {hit:.0%}" if hit is not None else ""), style="dim")
    parts: list[Text] = [head, Text("  ") + bar + tail, sub, *legend]
    if ledger.get("biggest"):
        parts.append(Text("  最占地方的：", style="dim"))
        parts.extend(Text(f"    {b['label'][:40]:<40} {_k(b['tokens']):>7}", style="dim") for b in ledger["biggest"])
    return Group(*parts)
