"""终端版 aila：交互模式和一次性（-p）模式。

  aila                        在当前目录开一个会话，交互着做
  aila -p "任务"              做完就退出：最后的回答打到标准输出，过程打到标准错误；退出码 0 = 做完了
  aila -c                     接着这个目录最近的会话
  aila -r [会话id]            接着某个会话（不给 id 就列出这个目录最近的会话）
  选项：--mode read_only|ask|auto  --cwd 目录  --local（不连 AI 服务）  --service（一定连 AI 服务）
        --json（-p 时按行输出 JSON 事件，给脚本用）

她做事的时候按 Esc（或 Ctrl+C）打断；要你确认时按 1 可以 / 2 以后都可以 / 3 或 Esc 不要。
"""

import argparse
import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import FileHistory, InMemoryHistory
from prompt_toolkit.input import Input, create_input
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.output import Output
from rich.console import Console
from rich.table import Table
from rich.text import Text

from ..runtime.permissions import MODE_LABELS, MODES
from ..runtime.pricing import validate_budget
from ..runtime.session import SESSION_ID, latest_turn, same_project, turn_edits
from .backend import Backend, BackendError, connect
from ..runtime.planning import plan_state
from .commands import COMMANDS, INIT_PROMPT, KEYS, changed_files, latest_todos, next_mode, parse, usage_lines
from .render import Renderer
from .views import build_trace, ledger_view, trace_view

INTERRUPT_KEYS = {Keys.Escape, Keys.ControlC}


class KeyWatcher:
    """她做事的时候读键盘：Esc / Ctrl+C 打断；要确认时读一个键"""

    def __init__(self, inp: Input) -> None:
        self.inp = inp
        self.on_interrupt: Callable[[], None] = lambda: None
        self._waiting: asyncio.Future[str] | None = None

    @contextmanager
    def watching(self, on_interrupt: Callable[[], None]):  # noqa: ANN201
        self.on_interrupt = on_interrupt
        loop = asyncio.get_running_loop()

        def ready() -> None:
            self._handle(self.inp.read_keys())
            # 单独的 Esc 要等一下才能确定不是方向键这类组合键的开头
            loop.call_later(0.08, lambda: self._handle(self.inp.flush_keys()))

        with self.inp.raw_mode(), self.inp.attach(ready):
            yield

    def _handle(self, presses: list[Any]) -> None:
        for press in presses:
            key = press.key  # Keys 里的一项（Esc、回车……），或者一个字符
            if self._waiting is not None and not self._waiting.done():
                self._waiting.set_result(key.value if isinstance(key, Keys) else str(key))
            elif key in INTERRUPT_KEYS:
                self.on_interrupt()

    async def key(self) -> str:
        self._waiting = asyncio.get_running_loop().create_future()
        try:
            return await self._waiting
        finally:
            self._waiting = None


class SlashCompleter(Completer):
    def __init__(self, computer: Callable[[], bool] = lambda: False,
                 custom: Callable[[], dict[str, str]] = dict) -> None:
        self.computer = computer
        self.custom = custom  # 自定义命令：/名字 → 一句说明

    def get_completions(self, document, complete_event):  # noqa: ANN001, ANN201
        text = document.text_before_cursor
        if text.startswith("/") and " " not in text:
            for name, desc in COMMANDS.items():
                if name.startswith(text.lower()):
                    yield Completion(name, start_position=-len(text), display_meta=desc[:30])
            for name, desc in self.custom().items():
                if name.lower().startswith(text.lower()):
                    yield Completion(name, start_position=-len(text), display_meta=f"自定义：{desc}"[:30])
        elif text.startswith("/mode "):
            word = text[6:]
            for mode in MODES:
                if self.computer() and mode == "auto":
                    continue
                if mode.startswith(word):
                    yield Completion(mode, start_position=-len(word), display_meta=MODE_LABELS[mode])


class Terminal:
    def __init__(
        self,
        backend: Backend,
        console: Console,
        info: dict[str, Any],
        inp: Input | None = None,
        output: Output | None = None,
        history: Path | None = None,
    ) -> None:
        self.backend = backend
        self.console = console
        self.info = info
        self.renderer = Renderer(console)
        self.inp = inp or create_input()
        self.watcher = KeyWatcher(self.inp)
        self._last_ctrl_c = 0.0
        self._resume_ids: list[str] | None = None
        # 自定义命令（/名字 → 说明）：开始时读一次，/commands 时重新读
        self.custom: dict[str, str] = {}
        self.prompt: PromptSession[str] = PromptSession(
            history=FileHistory(str(history)) if history else InMemoryHistory(),
            completer=SlashCompleter(lambda: self.info.get("kind") == "computer", lambda: self.custom),
            complete_while_typing=True,
            key_bindings=self._bindings(),
            bottom_toolbar=self._toolbar,
            prompt_continuation="  ",
            input=self.inp,
            output=output,
        )

    @property
    def sid(self) -> str:
        return str(self.info["id"])

    # ---------- 输入框 ----------

    def _bindings(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add("s-tab")
        def _(event: Any) -> None:
            mode = next_mode(self.info.get("mode", "ask"), computer=self.info.get("kind") == "computer") or "ask"
            self.info["mode"] = mode
            asyncio.ensure_future(self._quiet_set_mode(mode))
            event.app.invalidate()

        @kb.add("c-j")
        @kb.add("escape", "enter")
        def _(event: Any) -> None:
            event.current_buffer.insert_text("\n")

        return kb

    def _toolbar(self) -> Any:
        mode = self.info.get("mode", "ask")
        return [("class:bottom-toolbar", f" {MODE_LABELS.get(mode, mode)}（Shift+Tab 切换） · {self.backend.label} · /help ")]

    async def _quiet_set_mode(self, mode: str) -> None:
        try:
            await self.backend.set_mode(self.sid, mode)
        except BackendError as err:
            self.console.print(Text(str(err), style="red"))

    # ---------- 主循环 ----------

    async def load_commands(self) -> dict[str, Any]:
        """读这个项目能用的自定义命令；读不到（服务没开新接口之类）就当没有"""
        try:
            found = await self.backend.commands(self.info.get("cwd"), self.info.get("kind", "project"))
        except BackendError:
            return {"commands": []}
        self.custom = {f"/{c['name']}": c["description"] for c in found.get("commands", [])}
        return found

    async def loop(self) -> int:
        await self.load_commands()
        while True:
            try:
                text = await self.prompt.prompt_async("> ")
            except KeyboardInterrupt:
                now = asyncio.get_running_loop().time()
                if now - self._last_ctrl_c < 2:
                    return 0
                self._last_ctrl_c = now
                self.console.print(Text("再按一次 Ctrl+C 退出", style="dim"))
                continue
            except EOFError:
                return 0
            text = text.strip()
            if not text:
                continue
            if text.startswith("/") and not text.startswith("//"):
                if await self.command(text) == "exit":
                    return 0
                continue
            await self.turn(text)

    async def turn(self, text: str, review: bool = False, plan: bool = False,
                   stream: AsyncIterator[dict[str, Any]] | None = None) -> str:
        """做一轮（按计划做时是连着好几轮），返回最后一轮结束的原因"""
        interrupted = False

        def interrupt() -> None:
            nonlocal interrupted
            if not interrupted:
                interrupted = True
                self.renderer.stop_waiting()
                self.console.print(Text("正在打断……", style="yellow"))
                asyncio.ensure_future(self.backend.interrupt(self.sid))

        try:
            with self.watcher.watching(interrupt):
                async for e in stream if stream is not None else self.backend.run(self.sid, text, review, plan):
                    self.renderer.event(e)
                    if e["type"] == "confirm":
                        await self._confirm(e)
                    elif e["type"] == "mode":
                        self.info["mode"] = e["mode"]
                    elif e["type"] == "plan_pause":
                        self.console.print(Text(f"计划停在第 {int(e.get('stage', 0)) + 1} 阶段：{e.get('text', '')}。/plan continue 接着做", style="yellow"))
                    elif e["type"] == "plan_end":
                        self.console.print(Text("计划全部做完了。", style="green"))
                    elif e["type"] == "tool" and e.get("name") == "propose_plan" and e.get("plan"):
                        self.console.print(Text("/plan go 照这个做（/plan go 每段停：每阶段做完停一下），/plan no 不要，或者直接说要怎么改", style="yellow"))
        except BackendError as err:
            self.renderer.stop_waiting()
            self.console.print(Text(str(err), style="red"))
            return "error"
        return self.renderer.reason

    async def _plan(self, arg: str) -> None:
        """/plan 目标 · /plan go [每段停] · /plan no · /plan continue · /plan（看进度）"""
        verb, _, rest = arg.strip().partition(" ")
        if verb == "go":
            await self.turn("", stream=self.backend.plan_approve(self.sid, pause_each=rest.strip() in ("每段停", "--pause")))
        elif verb == "continue":
            await self.turn("", stream=self.backend.plan_continue(self.sid))
        elif verb == "no":
            await self.backend.plan_reject(self.sid)
            self.console.print(Text("不要这份计划了。直接说要做什么，或者 /plan 新的目标。", style="dim"))
        elif arg.strip():
            await self.turn(arg.strip(), plan=True)
        else:
            state = plan_state((await self.backend.session(self.sid))["events"])
            if state is None:
                self.console.print(Text("还没有计划。/plan 目标：她先只读地看一遍，交一份分阶段的计划给你批准。", style="dim"))
                return
            marks = {"done": "[x]", "running": "[→]", "blocked": "[!]", "pending": "[ ]"}
            head = {"proposed": "等你决定", "rejected": "没要", "approved": "全部做完了" if state.finished else "进行中"}[state.decision]
            self.console.print(Text(f"计划：{state.goal}（{head}）", style="bold"))
            for i, s in enumerate(state.stages, 1):
                self.console.print(Text(f"  {marks.get(s.status, '[ ]')} {i}. {s.title}", style="dim" if s.status == "done" else ""))

    async def _memory(self, arg: str) -> None:
        """/memory：看、加、删这个项目的记忆（新记的从下一个会话起带上）"""
        if self.info.get("kind") == "computer":
            raise BackendError("电脑任务不使用项目记忆。")
        cwd = str(self.info["cwd"])
        verb, _, rest = arg.strip().partition(" ")
        if verb == "add":
            if not rest.strip():
                self.console.print(Text("要记什么？/memory add 一句话", style="dim"))
                return
            note = await self.backend.remember(cwd, rest)
            self.console.print(Text(f"记下了：{note['text']}（下一个会话起带上）" if note.get("added") else "这条已经记过了。", style="dim"))
            return
        notes = await self.backend.memory(cwd)
        if verb == "forget":
            if not rest.strip().isdigit() or not 0 < int(rest) <= len(notes):
                self.console.print(Text("用 /memory 看编号，再用 /memory forget 编号 删掉一条。", style="dim"))
                return
            note = notes[int(rest) - 1]
            await self.backend.forget_memory(cwd, note["id"])
            self.console.print(Text(f"删掉了：{note['text']}", style="dim"))
            return
        if verb:
            self.console.print(Text("用法：/memory 看，/memory add 一句话，/memory forget 编号", style="dim"))
            return
        if not notes:
            self.console.print(Text("这个项目还没有记忆。她弄清了测试命令、代码约定这类事会自己记，你也可以 /memory add 一句话。", style="dim"))
            return
        for i, note in enumerate(notes, 1):
            who = "她记的" if note.get("source") == "agent" else "你加的"
            self.console.print(Text(f"{i:>2}. {note['text']}  ", style="").append(who, style="dim"))
        self.console.print(Text("/memory forget 编号 删掉一条；改动从下一个会话起生效", style="dim"))

    async def _mcp(self, arg: str) -> None:
        """/mcp：MCP 服务的状态和工具；/mcp restart 名字"""
        verb, _, name = arg.strip().partition(" ")
        if verb == "restart" and name.strip():
            self.renderer.waiting(f"正在重启 {name.strip()}…")
            try:
                servers = await self.backend.mcp_restart(name.strip())
            finally:
                self.renderer.stop_waiting()
        elif verb:
            self.console.print(Text("用法：/mcp 看状态，/mcp restart 名字 重启", style="dim"))
            return
        else:
            servers = await self.backend.mcp_status()
        if not servers:
            self.console.print(Text("还没有 MCP 服务。在设置页 → 编程 → 外部工具（MCP）里贴配置加一个（也可以手改 aila-ai/mcp.json，和对话共用）。", style="dim"))
            return
        labels = {"ready": "在运行", "starting": "正在启动", "failed": "没起来", "disabled": "停用了", "unsupported": "不支持", "off": "没启动"}
        for s in servers:
            line = Text(f"{s['name']}  ", style="bold").append(labels.get(s["status"], s["status"]), style="green" if s["status"] == "ready" else "yellow")
            line.append(f"  {s['command']}", style="dim")
            self.console.print(line)
            if s.get("error"):
                self.console.print(Text(f"  {s['error']}", style="red"))
            for t in s.get("tools", []):
                tags = "（只读）" if t.get("readOnly") else "（以后都不问）" if t.get("alwaysAllow") else ""
                self.console.print(Text(f"  · {t['name']}{tags}  ", style="").append(t.get("description", "")[:80], style="dim"))

    async def _confirm(self, e: dict[str, Any]) -> None:
        options = [("1", "可以")]
        if e.get("can_remember"):
            scope = "电脑任务" if self.info.get("kind") == "computer" else "这个项目"
            what = {"edit": "这个会话里改文件", "mcp": "这个外部工具"}.get(str(e.get("kind")), f"这条命令在{scope}里")
            options.append(("2", f"可以，{what}以后都不用问"))
        options.append(("3" if e.get("can_remember") else "2", "不要（Esc）——停下来，告诉她怎么做"))
        self.console.print(Text("   ".join(f"{k} {v}" for k, v in options), style="yellow"))
        no = options[-1][0]
        while True:
            key = (await self.watcher.key()).lower()
            if key in ("1", "y", "c-m", "enter"):
                approved, remember = True, False
            elif key in ("2", "a") and e.get("can_remember"):
                approved, remember = True, True
            elif key in (no, "n", "escape", "c-c"):
                approved, remember = False, False
            else:
                continue
            break
        if not await self.backend.answer(e["id"], approved, remember):
            self.console.print(Text("这个确认已经结束了（超时或者被打断）", style="dim"))

    # ---------- 斜杠命令 ----------

    async def command(self, text: str) -> str:
        name, arg = parse(text)
        try:
            if name == "/exit":
                return "exit"
            if name == "/help":
                self._help()
            elif name == "/mode":
                mode = next_mode(self.info.get("mode", "ask"), arg, computer=self.info.get("kind") == "computer")
                if mode is None:
                    self.console.print(Text("可选：" + "、".join(f"{k}（{v}）" for k, v in MODE_LABELS.items()
                                                        if self.info.get("kind") != "computer" or k != "auto"), style="dim"))
                else:
                    await self.backend.set_mode(self.sid, mode)
                    self.info["mode"] = mode
                    self.console.print(Text(f"权限模式：{MODE_LABELS[mode]}", style="dim"))
            elif name == "/compact":
                self.renderer.waiting("正在压缩上下文…")
                await self.backend.compact(self.sid)
                self.renderer.stop_waiting()
                self.console.print(Text("已压缩：之后用交接说明代替前面的对话", style="dim"))
            elif name == "/new":
                self.info = {**await self.backend.create(self.info["cwd"], self.info.get("mode", "ask"),
                                                       kind=self.info.get("kind", "project")),
                             "shell": self.info.get("shell", "")}
                self.renderer.header(self.info, self.backend.label)
            elif name == "/resume":
                await self._resume(arg)
            elif name == "/rename":
                renamed = await self.backend.rename(self.sid, arg)
                self.info["title"] = renamed["title"]
                self.console.print(Text(f"会话改名为：{renamed['title']}", style="dim"))
            elif name == "/delete":
                try:
                    answer = await self.prompt.prompt_async("删除当前会话和它的分支？[y/n] ")
                except (KeyboardInterrupt, EOFError):
                    answer = "n"
                if answer.strip().lower() not in ("y", "yes"):
                    self.console.print(Text("没删，继续这个会话。", style="dim"))
                else:
                    await self.backend.delete(self.sid)
                    self.info = await self.backend.create(self.info["cwd"], self.info.get("mode", "ask"),
                                                          kind=self.info.get("kind", "project"))
                    self._resume_ids = None
                    self.console.print(Text("删掉了，已在这个目录开一个新会话。", style="dim"))
                    self.renderer.header(self.info, self.backend.label)
            elif name == "/usage":
                for line in usage_lines(await self.backend.session(self.sid)):
                    self.console.print(Text(line, style="dim"))
            elif name == "/todo":
                todos = latest_todos((await self.backend.session(self.sid))["events"])
                if not todos:
                    self.console.print(Text("还没有待办清单。", style="dim"))
                for t in todos:
                    mark = {"completed": "☑", "in_progress": "◐"}.get(t["status"], "☐")
                    self.console.print(f"  {mark} {t['content']}")
            elif name == "/changes":
                files = changed_files((await self.backend.session(self.sid))["events"])
                if not files:
                    self.console.print(Text("这个会话里她还没改过文件。", style="dim"))
                for path, n in files:
                    self.console.print(f"  {path}" + (f"（{n} 次）" if n > 1 else ""))
            elif name == "/trace":
                turns = build_trace((await self.backend.session(self.sid))["events"])
                back = int(arg) if arg.isdigit() and int(arg) > 0 else 1
                if len(turns) < back:
                    self.console.print(Text("还没有做过事。" if not turns else f"只有 {len(turns)} 轮。", style="dim"))
                else:
                    self.console.print(trace_view(turns[-back]))
            elif name == "/context":
                self.console.print(ledger_view(await self.backend.context(self.sid)))
            elif name == "/init":
                if self.info.get("kind") == "computer":
                    raise BackendError("电脑任务不用写项目说明。直接说要做什么，或用 /plan 先出方案。")
                await self.turn(INIT_PROMPT)
            elif name == "/review":
                await self.turn(arg, review=True)
            elif name == "/plan":
                await self._plan(arg)
            elif name == "/merge":
                no_tests = arg in ("直接", "--no-tests", "no-tests")
                self.renderer.waiting("正在合并…" if no_tests else "正在跑测试…")
                event = await self.backend.merge(self.sid, run_tests=not no_tests)
                self.renderer.stop_waiting()
                self.renderer.event(event)
            elif name == "/discard":
                event = await self.backend.discard(self.sid)
                self.renderer.event(event)
                self.info["branch"] = None
            elif name == "/rules":
                groups = await self.backend.rules()
                scope = "computer" if self.info.get("kind") == "computer" else self.info["cwd"]
                mine = next((group for group in groups if _same_dir(group["project"], scope)), None)
                commands = mine["commands"] if mine else []
                if not arg:
                    if not commands:
                        place = "电脑任务" if self.info.get("kind") == "computer" else "这个项目"
                        self.console.print(Text(f"{place}还没有记住的命令。确认命令时选「以后都允许」才会记在这里。", style="dim"))
                    for i, command in enumerate(commands, 1):
                        self.console.print(Text(f"{i:>2}. {command}"))
                    if commands:
                        self.console.print(Text("/rules forget 编号 删掉一条", style="dim"))
                else:
                    parts = arg.split()
                    if len(parts) != 2 or parts[0] != "forget" or not parts[1].isdigit():
                        self.console.print(Text("用 /rules 查看编号，再用 /rules forget 编号 删掉一条。", style="dim"))
                    elif not 0 < int(parts[1]) <= len(commands):
                        self.console.print(Text("没有这个编号，用 /rules 看当前记住的命令。", style="dim"))
                    else:
                        command = commands[int(parts[1]) - 1]
                        await self.backend.forget_rule(mine["project"], command)
                        self.console.print(Text(f"删掉了记住的命令：{command}；下次执行时重新按权限模式确认。", style="dim"))
            elif name == "/memory":
                await self._memory(arg)
            elif name == "/mcp":
                await self._mcp(arg)
            elif name == "/undo":
                await self._undo()
            elif name == "/budget":
                if not arg:
                    info = await self.backend.session(self.sid)
                    amount = info.get("budget")
                else:
                    if arg.lower() == "off":
                        amount = None
                    else:
                        try:
                            amount = validate_budget(float(arg))
                        except (ValueError, TypeError):
                            raise BackendError("用 /budget 金额 设上限，金额要是非负有限数字；/budget off 取消上限。")
                    await self.backend.set_budget(self.sid, amount)
                    self.info["budget"] = amount
                self.console.print(Text("花费不限。" if amount is None else f"花费上限 ¥{amount:.6f}", style="dim"))
            elif name == "/commands":
                await self._commands()
            elif name in ("/commit", "/pr", "/pr-comments"):
                await self.turn(text)  # 请求由 AI 服务按改动清单、分支、PR 评论生成
            elif await self._is_custom(name):
                cmd = text.strip().split(maxsplit=1)[0]
                self.console.print(Text(f"按自定义命令 {cmd} 做：{self.custom.get(cmd, '')}", style="dim"))
                await self.turn(text)  # 原样发过去，AI 服务按命令文件展开
            else:
                self.console.print(Text(f"没有 {name} 这个命令，/help 看所有命令，/commands 看自定义命令", style="dim"))
        except BackendError as err:
            self.renderer.stop_waiting()
            self.console.print(Text(str(err), style="red"))
        return ""

    async def _undo(self) -> None:
        session = await self.backend.session(self.sid)
        if session.get("running"):
            raise BackendError("她还在做事，等这一轮结束再撤销。")
        events = session["events"]
        turn = latest_turn(events)
        files = sorted({edit["file"] for edit in turn_edits(turn)})
        if not files:
            raise BackendError("最近一轮没有改文件。")
        seq = turn[0]["seq"]
        if any(e["type"] == "undo" and e.get("turn_seq") == seq for e in events):
            raise BackendError("最近一轮已经撤销过了。")

        self.console.print(Text("准备撤销最近一轮对这些文件的改动："))
        for path in files:
            self.console.print(Text(f"  {path}"))
        self.console.print(Text("之后又被改过的文件会跳过。", style="dim"))
        if any(e["type"] == "tool" and e.get("name") == "run_command" and e.get("status") == "ok" for e in turn):
            self.console.print(Text("这一轮还跑过命令，命令做的事不会撤销。", style="yellow"))
        try:
            answer = await self.prompt.prompt_async("撤销这一轮？[y/n] ")
        except (KeyboardInterrupt, EOFError):
            answer = "n"
        if answer.strip().lower() not in ("y", "yes"):
            self.console.print(Text("没撤销。", style="dim"))
            return

        event = await self.backend.undo(self.sid, seq)
        restored, skipped = event["restored"], event["skipped"]
        self.console.print(Text(f"退回了 {len(restored)} 个文件。"))
        for path in restored:
            self.console.print(Text(f"  退回 {path}"))
        for path, reason in skipped.items():
            self.console.print(Text(f"  跳过 {path}：{reason}", style="yellow"))
        if event.get("commands"):
            self.console.print(Text("这一轮还跑过命令，命令做的事没有撤销。", style="yellow"))

    async def _is_custom(self, name: str) -> bool:
        """是不是自定义命令；不在记着的列表里就再读一遍（可能刚加了命令文件）"""
        if name.lower() not in {k.lower() for k in self.custom}:
            await self.load_commands()
        return name.lower() in {k.lower() for k in self.custom}

    async def _commands(self) -> None:
        found = await self.load_commands()
        table = Table(show_header=False, box=None, padding=(0, 2))
        for c in found.get("commands", []):
            table.add_row(Text(f"/{c['name']}", style="bold"), c["description"],
                          Text("项目" if c["source"] == "project" else "我的", style="dim"))
        if found.get("commands"):
            self.console.print(table)
        else:
            self.console.print(Text("还没有自定义命令。", style="dim"))
        where = [f"项目里：{found['project_dir']}"] if found.get("project_dir") else []
        if found.get("user_dir"):
            where.append(f"每个项目都能用：{found['user_dir']}")
        self.console.print(Text(
            "把常做的流程写成 <名字>.md 放进去，就能用 /名字 让她照着做。" + "；".join(where)
            + "。开头可以用两行 --- 包住一行 description: 一句说明；正文里的 $ARGUMENTS 换成命令后面写的内容。", style="dim"))

    def _help(self) -> None:
        table = Table(show_header=False, box=None, padding=(0, 2))
        for name, desc in COMMANDS.items():
            if self.info.get("kind") == "computer":
                if name in ("/init", "/review", "/memory", "/mcp", "/merge", "/discard", "/commit", "/pr", "/pr-comments"):
                    continue
                if name == "/mode":
                    desc = "换权限模式：/mode read_only | ask；不带参数或 Shift+Tab 切换"
            table.add_row(Text(name, style="bold"), desc)
        if self.custom:
            table.add_row("", "")
            for name, desc in self.custom.items():
                table.add_row(Text(name, style="bold"), f"自定义：{desc}")
        table.add_row("", "")
        for key, desc in KEYS:
            table.add_row(Text(key, style="bold"), desc)
        self.console.print(table)

    async def _resume(self, arg: str) -> None:
        searching = bool(arg and not arg.isdigit() and not SESSION_ID.fullmatch(arg))
        found = await self.backend.search_sessions(arg, self.info["cwd"]) if searching else await self.backend.sessions()
        mine = [s for s in found if _same_dir(s.get("cwd", ""), self.info["cwd"])
                and s.get("kind", "project") == self.info.get("kind", "project")]
        if not arg or searching:
            key = arg.casefold()
            matches = [s for s in mine if key in s["title"].casefold() or key in s.get("cwd", "").casefold()]
            self._resume_ids = [s["id"] for s in matches[:10]]
            if not matches:
                self.console.print(Text("没找到匹配的会话。" if arg else "这个目录还没有别的会话。", style="dim"))
            for i, s in enumerate(matches[:10], 1):
                when = datetime.fromtimestamp(s["updated"]).strftime("%m-%d %H:%M") if s.get("updated") else ""
                self.console.print(Text.assemble((f"{i:>2}. ", "bold"), s["title"], (f"  {when} · {s['id']}", "dim")))
            self.console.print(Text("/resume 编号 打开", style="dim"))
            return
        if arg.isdigit():
            ids = self._resume_ids if self._resume_ids is not None else [s["id"] for s in mine]
            if not 0 < int(arg) <= len(ids):
                self.console.print(Text("没有这个编号，先用 /resume 看会话。", style="dim"))
                return
            target = ids[int(arg) - 1]
        else:
            target = arg
        info = await self.backend.session(target)
        if not _same_dir(info.get("cwd", ""), self.info["cwd"]):
            self._resume_ids = None
        self.info = {**info, "shell": info.get("shell") or self.info.get("shell", "")}
        self.renderer.header(self.info, self.backend.label)
        self.renderer.replay(info.get("events", []))


def _same_dir(a: str, b: str) -> bool:
    return same_project(a, b)


# ---------- 一次性模式 ----------


async def headless(backend: Backend, sid: str, text: str, json_lines: bool = False, err: Console | None = None,
                   *, computer: bool = False) -> int:
    """做完就退出。最后的回答 → 标准输出；过程 → 标准错误。要确认的操作没法问，一律不执行"""
    err = err or Console(stderr=True, highlight=False)
    renderer = Renderer(err, show_text=False)
    final, reason = "", ""
    try:
        async for e in backend.run(sid, text):
            if json_lines:
                print(json.dumps(e, ensure_ascii=False), flush=True)
            else:
                renderer.event(e)
            if e["type"] == "confirm":
                await backend.answer(e["id"], False)
                if not json_lines:
                    hint = "用交互模式来确认" if computer else "需要的话加 --mode auto，或者用交互模式"
                    err.print(Text(f"-p 模式下没法问你，这个操作没有执行（{hint}）", style="yellow"))
            elif e["type"] == "assistant" and e.get("text", "").strip():
                final = e["text"]
            elif e["type"] == "turn_end":
                reason = e["reason"]
    except BackendError as error:
        err.print(Text(str(error), style="red"))
        return 1
    if not json_lines:
        print(final.strip())
    return 0 if reason == "done" else 1


# ---------- 入口 ----------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="aila", description="艾拉：在终端里帮你写代码、操作电脑")
    p.add_argument("-p", "--prompt", help="做完这个任务就退出")
    p.add_argument("-c", "--continue", dest="cont", action="store_true", help="接着这个目录最近的会话")
    p.add_argument("-r", "--resume", nargs="?", const="", help="接着某个会话（不给 id 就列出来）")
    p.add_argument("--cwd", default=os.getcwd(), help="项目目录（默认当前目录）")
    p.add_argument("--mode", choices=MODES, help="权限模式：read_only 只读 / ask 先问再改（默认）/ auto 工作区内自动")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--local", action="store_true", help="不连 AI 服务，在终端里自己跑")
    g.add_argument("--service", action="store_true", help="一定连 AI 服务（没开就报错）")
    p.add_argument("--json", action="store_true", help="-p 时按行输出 JSON 事件")
    p.add_argument("--branch", action="store_true", help="在新分支上做：你的代码不动，/merge 时跑测试、合并回来")
    p.add_argument("--computer", action="store_true", help="开电脑任务：从用户目录开始，只读或先问再改")
    p.add_argument("--budget", type=_budget_arg, help="会话累计花费上限（元），不填就不限")
    args = p.parse_args(argv)
    if args.computer and args.mode == "auto":
        p.error("电脑任务只能选 read_only 或 ask。")
    if args.computer and args.branch:
        p.error("电脑任务不能在新分支上做。")
    return args


def _budget_arg(value: str) -> float:
    try:
        return float(validate_budget(float(value)))
    except (ValueError, TypeError):
        raise argparse.ArgumentTypeError("花费上限要是非负的有限数字（元）。") from None


async def open_session(backend: Backend, args: argparse.Namespace, console: Console) -> tuple[dict[str, Any], bool]:
    """返回（会话信息，是不是接着以前的）"""
    kind = "computer" if args.computer else "project"
    cwd = str(Path.home().resolve() if args.computer else Path(args.cwd).resolve())
    resumed: dict[str, Any] | None = None
    if args.resume:
        resumed = await backend.session(args.resume)
    elif args.cont or args.resume == "":
        mine = [s for s in await backend.sessions() if _same_dir(s.get("cwd", ""), cwd)
                and s.get("kind", "project") == kind]
        if args.resume == "" and mine:
            for i, s in enumerate(mine[:10], 1):
                console.print(Text.assemble((f"{i:>2}. ", "bold"), s["title"], (f"  {s['id']}", "dim")))
            pick = (await asyncio.to_thread(input, "接着哪个（编号，回车 = 1）> ")).strip() or "1"
            resumed = await backend.session(mine[int(pick) - 1]["id"]) if pick.isdigit() and 0 < int(pick) <= len(mine[:10]) else None
        elif mine:
            resumed = await backend.session(mine[0]["id"])
    if resumed:
        if args.mode and args.mode != resumed.get("mode"):
            await backend.set_mode(resumed["id"], args.mode)
            resumed["mode"] = args.mode
        if args.budget is not None:
            await backend.set_budget(resumed["id"], args.budget)
            resumed["budget"] = args.budget
        return resumed, True
    return await backend.create(cwd, args.mode or "ask", args.branch, budget=args.budget, kind=kind), False


async def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    console = Console(highlight=False)
    try:
        backend = await connect("local" if args.local else "service" if args.service else "auto")
    except BackendError as err:
        console.print(Text(str(err), style="red"))
        return 1
    try:
        try:
            info, resumed = await open_session(backend, args, console)
        except BackendError as err:
            console.print(Text(str(err), style="red"))
            return 1
        if args.prompt:
            return await headless(backend, info["id"], args.prompt, args.json, computer=info.get("kind") == "computer")
        from ..deps import settings

        term = Terminal(backend, console, info, history=settings.data_dir / "cli_history")
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        term.renderer.header(info, backend.label)
        if resumed:
            term.renderer.replay(info.get("events", []))
        return await term.loop()
    finally:
        await backend.close()


def run() -> None:
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
