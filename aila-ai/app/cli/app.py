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
from collections.abc import Callable
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
from .backend import Backend, BackendError, connect
from .commands import COMMANDS, INIT_PROMPT, KEYS, changed_files, latest_todos, next_mode, parse, usage_lines
from .render import Renderer

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
    def get_completions(self, document, complete_event):  # noqa: ANN001, ANN201
        text = document.text_before_cursor
        if text.startswith("/") and " " not in text:
            for name, desc in COMMANDS.items():
                if name.startswith(text.lower()):
                    yield Completion(name, start_position=-len(text), display_meta=desc[:30])
        elif text.startswith("/mode "):
            word = text[6:]
            for mode in MODES:
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
        self.prompt: PromptSession[str] = PromptSession(
            history=FileHistory(str(history)) if history else InMemoryHistory(),
            completer=SlashCompleter(),
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
            mode = next_mode(self.info.get("mode", "ask")) or "ask"
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

    async def loop(self) -> int:
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

    async def turn(self, text: str) -> str:
        """做一轮，返回结束的原因"""
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
                async for e in self.backend.run(self.sid, text):
                    self.renderer.event(e)
                    if e["type"] == "confirm":
                        await self._confirm(e)
                    elif e["type"] == "mode":
                        self.info["mode"] = e["mode"]
        except BackendError as err:
            self.renderer.stop_waiting()
            self.console.print(Text(str(err), style="red"))
            return "error"
        return self.renderer.reason

    async def _confirm(self, e: dict[str, Any]) -> None:
        options = [("1", "可以")]
        if e.get("can_remember"):
            what = "这个会话里改文件" if e.get("kind") == "edit" else "这条命令在这个项目里"
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
                mode = next_mode(self.info.get("mode", "ask"), arg)
                if mode is None:
                    self.console.print(Text("可选：" + "、".join(f"{k}（{v}）" for k, v in MODE_LABELS.items()), style="dim"))
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
                self.info = {**await self.backend.create(self.info["cwd"], self.info.get("mode", "ask")),
                             "shell": self.info.get("shell", "")}
                self.renderer.header(self.info, self.backend.label)
            elif name == "/resume":
                await self._resume(arg)
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
            elif name == "/init":
                await self.turn(INIT_PROMPT)
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
            else:
                self.console.print(Text(f"没有 {name} 这个命令，/help 看所有命令", style="dim"))
        except BackendError as err:
            self.renderer.stop_waiting()
            self.console.print(Text(str(err), style="red"))
        return ""

    def _help(self) -> None:
        table = Table(show_header=False, box=None, padding=(0, 2))
        for name, desc in COMMANDS.items():
            table.add_row(Text(name, style="bold"), desc)
        table.add_row("", "")
        for key, desc in KEYS:
            table.add_row(Text(key, style="bold"), desc)
        self.console.print(table)

    async def _resume(self, arg: str) -> None:
        mine = [s for s in await self.backend.sessions() if _same_dir(s.get("cwd", ""), self.info["cwd"])]
        if not arg:
            if not mine:
                self.console.print(Text("这个目录还没有别的会话。", style="dim"))
            for i, s in enumerate(mine[:10], 1):
                when = datetime.fromtimestamp(s["updated"]).strftime("%m-%d %H:%M") if s.get("updated") else ""
                self.console.print(Text.assemble((f"{i:>2}. ", "bold"), s["title"], (f"  {when} · {s['id']}", "dim")))
            self.console.print(Text("/resume 编号 打开", style="dim"))
            return
        target = mine[int(arg) - 1]["id"] if arg.isdigit() and 0 < int(arg) <= len(mine) else arg
        info = await self.backend.session(target)
        self.info = {**info, "shell": info.get("shell") or self.info.get("shell", "")}
        self.renderer.header(self.info, self.backend.label)
        self.renderer.replay(info.get("events", []))


def _same_dir(a: str, b: str) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return a == b


# ---------- 一次性模式 ----------


async def headless(backend: Backend, sid: str, text: str, json_lines: bool = False, err: Console | None = None) -> int:
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
                    err.print(Text("-p 模式下没法问你，这个操作没有执行（需要的话加 --mode auto，或者用交互模式）", style="yellow"))
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
    p = argparse.ArgumentParser(prog="aila", description="艾拉：在终端里帮你写代码")
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
    return p.parse_args(argv)


async def open_session(backend: Backend, args: argparse.Namespace, console: Console) -> tuple[dict[str, Any], bool]:
    """返回（会话信息，是不是接着以前的）"""
    cwd = str(Path(args.cwd).resolve())
    resumed: dict[str, Any] | None = None
    if args.resume:
        resumed = await backend.session(args.resume)
    elif args.cont or args.resume == "":
        mine = [s for s in await backend.sessions() if _same_dir(s.get("cwd", ""), cwd)]
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
        return resumed, True
    return await backend.create(cwd, args.mode or "ask", args.branch), False


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
            return await headless(backend, info["id"], args.prompt, args.json)
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
