"""把运行时的事件画到终端上（rich）。

- 她说的话按 Markdown 显示：边收边按段落输出（代码块没闭合时不切，免得把代码块切成两半）
- 工具：一行「● 做什么」，下面「⎿ 结果」；改文件给彩色 diff，跑命令给最后几行输出，待办给勾选清单
- 每一轮结束给一行统计：几步、多久、输入多少（命中缓存多少）、输出多少
- 重新打开会话时，用同样的方式把日志重放一遍（没有逐字事件，按完整的 assistant 事件显示）
"""

import json
import re
import time
from typing import Any

from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from ..runtime.permissions import MODE_LABELS

Event = dict[str, Any]

DIFF_LINES = 40
OUTPUT_TAIL = 8
# 子 Agent 的报告在终端里显示几行（完整的在 /trace 和工作窗口里）
EXPLORE_LINES = 8
FENCE = re.compile(r"^\s*(```|~~~)", re.MULTILINE)
REASONS = {
    "done": ("✓", "green", "做完了"),
    "max_steps": ("■", "yellow", "步数到上限了：说「继续」可以接着做"),
    "denied": ("■", "yellow", "她要做的事连着被权限挡下，先停下了：换个权限模式，或者说清楚接下来怎么做"),
    "budget": ("■", "yellow", "花到上限了：先调高上限，再说「继续」"),
    "rejected": ("■", "yellow", "停下来了，等你说接下来怎么做"),
    "timeout": ("■", "yellow", "一直没等到你的回答，先停下了"),
    "interrupted": ("■", "yellow", "被你打断了：说「继续」可以接着做"),
    "error": ("✗", "red", "出错了"),
}
STATUS_STYLE = {"ok": "green", "error": "red", "denied": "red", "rejected": "yellow", "timeout": "yellow",
                "skipped": "yellow", "interrupted": "yellow"}


def compact_number(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def diff_text(diff: str, limit: int = DIFF_LINES) -> Text:
    out = Text()
    lines = diff.splitlines()
    for line in lines[:limit]:
        style = ("bold" if line.startswith(("+++", "---")) else "green" if line.startswith("+")
                 else "red" if line.startswith("-") else "cyan" if line.startswith("@@") else "dim")
        out.append(line + "\n", style=style)
    if len(lines) > limit:
        out.append(f"…还有 {len(lines) - limit} 行\n", style="dim")
    return out


def split_markdown(buffer: str) -> tuple[str, str]:
    """能先显示的完整段落，和还要等的剩余部分。代码块没闭合时一个字都不切"""
    cut = buffer.rfind("\n\n")
    while cut > 0:
        head = buffer[:cut]
        if len(FENCE.findall(head)) % 2 == 0:
            return head, buffer[cut + 2 :]
        cut = buffer.rfind("\n\n", 0, cut)
    return "", buffer


class Renderer:
    def __init__(self, console: Console, show_text: bool = True) -> None:
        self.console = console
        self.show_text = show_text
        self._buffer = ""
        self._streamed = False
        self._calls: dict[str, tuple[str, dict[str, Any]]] = {}
        self._actions: dict[str, str] = {}
        self._status: Any = None
        self._replaying = False
        self.reset_turn()

    def reset_turn(self) -> None:
        self.turn_started = time.time()
        self.steps = 0
        self.prompt = self.hit = self.completion = 0
        self.estimated = False
        self.last_text = ""
        self.reason = ""

    # ---------- 等待提示 ----------

    def waiting(self, text: str) -> None:
        self.stop_waiting()
        if self.console.is_terminal and not self._replaying:
            self._status = self.console.status(Text(text, style="dim"), spinner="dots")
            self._status.start()

    def stop_waiting(self) -> None:
        if self._status is not None:
            self._status.stop()
            self._status = None

    # ---------- 事件 ----------

    def header(self, info: dict[str, Any], backend: str) -> None:
        mode = MODE_LABELS.get(info.get("mode", ""), info.get("mode", ""))
        title = (Text.assemble(("艾拉", "bold magenta"), "  电脑任务") if info.get("kind") == "computer" else
                 Text.assemble(("艾拉", "bold magenta"), "  在 ", (info.get("cwd", ""), "bold"), " 里工作"))
        meta = Text(f"{mode} · 模型 {info.get('model', '')} · 命令用 {info.get('shell', '')} · {backend} · 会话 {info.get('id', '')}",
                    style="dim")
        tips = Text("/help 看命令 · Shift+Tab 换权限模式 · Esc 打断 · Ctrl+J 换行", style="dim")
        if info.get("branch"):
            tips = Text(f"在新分支 {info['branch']} 上做，你的代码不动 · /merge 跑测试并合并 · /discard 放弃分支\n", style="cyan") + tips
        self.console.print(Panel(Group(title, meta, tips), border_style="magenta", expand=False))

    def event(self, e: Event) -> None:
        kind = e.get("type")
        handler = getattr(self, f"_on_{kind}", None)
        if handler:
            handler(e)

    def replay(self, events: list[Event]) -> None:
        """重新打开会话：把之前的过程再显示一遍"""
        self._replaying = True
        try:
            for e in events:
                if e.get("type") in ("user", "assistant", "usage", "tool", "compaction", "turn_end", "confirm_end",
                                     "merge", "branch_closed"):
                    self.event(e)
        finally:
            self._replaying = False
        self.reset_turn()

    def _on_user(self, e: Event) -> None:
        self.reset_turn()
        self.turn_started = float(e.get("t") or time.time())  # 重放时按日志里的时间算
        if self.show_text:
            # /review 的请求是固定的一大段：只显示一句
            shown = e.get("title") if e.get("review") else e["text"]
            self.console.print(Text("> " + str(shown), style="bold"))
        self.waiting("思考中…")

    def _on_text_delta(self, e: Event) -> None:
        self.stop_waiting()
        self._streamed = True
        if not self.show_text:
            return
        self._buffer += e["text"]
        ready, self._buffer = split_markdown(self._buffer)
        if ready.strip():
            self.console.print(Markdown(ready))

    def _on_reasoning_delta(self, _: Event) -> None:
        if self._status is None:
            self.waiting("思考中…")

    def _on_assistant(self, e: Event) -> None:
        self.stop_waiting()
        self.steps += 1
        text = e.get("text", "")
        if text.strip():
            self.last_text = text
        if self.show_text:
            rest = self._buffer if self._streamed else text
            if rest.strip():
                self.console.print(Markdown(rest))
        self._buffer = ""
        self._streamed = False
        for c in e.get("tool_calls", []):
            try:
                args = json.loads(c.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            self._calls[c["id"]] = (c["name"], args if isinstance(args, dict) else {})

    def _on_usage(self, e: Event) -> None:
        self.prompt += int(e.get("prompt", 0))
        self.hit += int(e.get("cache_hit", 0))
        self.completion += int(e.get("completion", 0))
        self.estimated = self.estimated or bool(e.get("estimated"))

    def _on_tool_start(self, e: Event) -> None:
        self._actions[e["id"]] = e.get("status", e.get("name", ""))
        self.waiting(f"● {e.get('status', '')}…")

    def _on_confirm(self, e: Event) -> None:
        self.stop_waiting()
        body: list[Any] = []
        if e.get("reason"):
            body.append(Text(e["reason"], style="yellow"))
        detail = str(e.get("detail", ""))
        if e.get("kind") == "edit" and ("@@" in detail or detail.startswith("---")):
            body.append(diff_text(detail, 60))
        elif e.get("kind") == "command":
            first, _, rest = detail.partition("\n")
            body.append(Text(first, style="bold"))
            if rest:
                body.append(Text(rest, style="dim"))
        else:
            body.append(Text(detail))
        self.console.print(Panel(Group(*body), title=f"要{e.get('action', '')}，可以吗？", title_align="left",
                                 border_style="yellow"))

    def _on_confirm_end(self, e: Event) -> None:
        words = {"approved": "可以" + ("（以后都不用问）" if e.get("remember") else ""), "rejected": "不要", "timeout": "没等到回答"}
        self.console.print(Text(f"  ⎿ {words.get(e.get('result', ''), e.get('result', ''))}", style="dim"))

    def _on_tool(self, e: Event) -> None:
        self.stop_waiting()
        name, args = self._calls.get(e["id"], (e.get("name", ""), {}))
        action = self._actions.pop(e["id"], "") or _action(name, args)
        status = e.get("status", "ok")
        style = STATUS_STYLE.get(status, "white")
        self.console.print(Text.assemble(("● ", style), (action, "bold")))
        content = str(e.get("content", ""))
        if status != "ok":
            self.console.print(Text("  ⎿ " + _first(content), style=style))
            if name == "run_command" and status == "error":
                self._tail(content)
            return
        if name in ("edit_file", "write_file"):
            self.console.print(Text("  ⎿ " + _first(content), style="dim"))
            if e.get("diff"):
                self.console.print(diff_text(e["diff"]), end="")
        elif name == "run_command":
            self.console.print(Text("  ⎿ " + _first(content), style="dim"))
            self._tail(content)
        elif name == "todo_write":
            for item in e.get("todos", []):
                mark, style_ = {"completed": ("☑", "dim strike"), "in_progress": ("◐", "bold cyan")}.get(item["status"], ("☐", ""))
                self.console.print(Text(f"  {mark} {item['content']}", style=style_))
        elif name == "explore":
            report = [line for line in content.splitlines() if line.strip()]
            for line in report[:EXPLORE_LINES]:
                self.console.print(Text("  ⎿ " + line[:120], style="dim"))
            more = f"，报告还有 {len(report) - EXPLORE_LINES} 行" if len(report) > EXPLORE_LINES else ""
            self.console.print(Text(f"    （助手用 {e.get('model', '')} 查了 {e.get('steps', 0)} 步{more}）", style="dim"))
        elif name == "read_file":
            lines = sum(1 for line in content.splitlines() if re.match(r"^\s*\d+\t", line))
            self.console.print(Text(f"  ⎿ 读了 {lines} 行" if lines else "  ⎿ " + _first(content), style="dim"))
        else:
            self.console.print(Text("  ⎿ " + _first(content), style="dim"))
        self.waiting("思考中…")  # 工具做完，轮到她想下一步

    def _tail(self, content: str) -> None:
        body = content.split("\n", 1)[1] if "\n" in content else ""
        lines = [line for line in body.splitlines() if line.strip()]
        for line in lines[-OUTPUT_TAIL:]:
            self.console.print(Text("    " + line[:200], style="dim"))

    def _on_merge(self, e: Event) -> None:
        self.stop_waiting()
        style = "green" if e.get("merged") else "yellow"
        self.console.print(Text(("✓ 合并了：" if e.get("merged") else "■ 没有合并：") + str(e.get("reason", "")), style=style))
        for t in e.get("tests", []):
            self.console.print(Text(f"  {'✓' if t['ok'] else '✗'} {t['label']} · {t['seconds']} 秒", style="green" if t["ok"] else "red"))
            if not t["ok"]:
                self._tail("\n" + t.get("tail", ""))
        if e.get("files"):
            self.console.print(Text("  文件：" + "、".join(e["files"]), style="dim"))

    def _on_branch_closed(self, e: Event) -> None:
        self.console.print(Text(f"放弃了分支 {e.get('branch', '')}：你的代码保持原样", style="dim"))

    def _on_compaction(self, _: Event) -> None:
        self.console.print(Text("（上下文快满了：已经用交接说明代替前面的对话）", style="dim"))

    def _on_mode(self, e: Event) -> None:
        self.console.print(Text(f"权限模式：{MODE_LABELS.get(e['mode'], e['mode'])}", style="dim"))

    def _on_turn_end(self, e: Event) -> None:
        self.stop_waiting()
        if self._buffer.strip() and self.show_text:
            self.console.print(Markdown(self._buffer))
        self._buffer = ""
        self.reason = e.get("reason", "")
        mark, style, words = REASONS.get(self.reason, ("■", "yellow", self.reason))
        if self.reason == "error" and e.get("error"):
            words = f"出错了：{e['error']}"
        ended = float(e.get("t") or time.time())
        stats = [f"{self.steps} 步", f"{max(0.0, ended - self.turn_started):.1f} 秒"]
        if self.prompt:
            rate = self.hit / self.prompt
            stats.append(f"输入 {compact_number(self.prompt)}（命中缓存 {rate:.0%}{'，估算' if self.estimated else ''}）")
            stats.append(f"输出 {compact_number(self.completion)}")
        if e.get("cost") is not None:
            stats.append("不到 ¥0.000001" if 0 < e["cost"] < 0.000001 else f"¥{e['cost']:.6f}")
        self.console.print(Text.assemble((f"{mark} {words}", style), ("  " + " · ".join(stats), "dim")))


def _first(text: str, limit: int = 160) -> str:
    line = next((s for s in text.splitlines() if s.strip()), "")
    return line[:limit] + ("…" if len(line) > limit else "")


def _action(name: str, args: dict[str, Any]) -> str:
    """重放日志时没有 tool_start：按参数拼出做了什么"""
    return {
        "read_file": f"读 {args.get('path', '')}",
        "list_files": f"找文件 {args.get('pattern') or '*'}",
        "search": f"搜「{str(args.get('pattern', ''))[:40]}」",
        "edit_file": f"改 {args.get('path', '')}",
        "write_file": f"写 {args.get('path', '')}",
        "run_command": ("后台：" if args.get("background") else "") + str(args.get("description") or args.get("command", ""))[:60],
        "todo_write": "更新待办清单",
        "explore": f"派助手去查「{str(args.get('task', '')).strip()[:30]}」",
        "web_search": f"搜网页「{str(args.get('query', ''))[:30]}」",
        "web_fetch": f"读网页 {str(args.get('url', ''))[:60]}",
        "job_output": f"看后台任务 {args.get('id', '')} 的输出",
        "job_stop": f"停掉后台任务 {args.get('id', '')}",
    }.get(name, name)
