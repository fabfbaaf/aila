"""本机 Agent（Codex、Claude Code）当一个编程会话的「大脑」：通过 ACP 适配器跑一轮，把它的事件翻成艾拉的事件。

任务页、终端、手机看到的还是同一种会话日志：她说的话、工具卡片（命令、改文件、读文件）、待办、确认、一轮结束。
和艾拉自己的 Agent（loop.py）的区别只在谁在想、谁在动手：这里是 Codex / Claude Code 自己的工具在干活。

权限还是艾拉说了算：
- Codex 开在只读沙箱里，并且告诉它「要改东西就请求批准」；Claude Code 开在「每次改动前都问」的模式
- 它们发来的每个批准请求都按艾拉的权限规则（permissions.Policy）决定：允许、拒绝，或者在任务页里问你
- 危险命令（删除、推送、发布……）任何模式都要问；问你时给你看的 diff、命令由这里的代码生成，不用它的描述
- 它在只读沙箱里自己跑的查看类命令不经过我们（和艾拉的「读不用问」一样）

续跑：ACP 会话的编号记在日志里（engine_session），艾拉重启、适配器换了进程以后用「加载会话」接着之前的上下文说；
加载时它会把以前的对话重放一遍，这些不再记进日志。
打断：取消这一轮（session/cancel），进程留着，下次接着用。
"""

from __future__ import annotations

import asyncio
import difflib
import json
import os
import re
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, AsyncIterator, Callable

import acp
from acp import schema

from ..confirm import Confirmations
from .localagents import ENGINES, LocalAgentError, launch
from .permissions import Policy
from .session import Event, Session

INIT_TIMEOUT = 60
SESSION_TIMEOUT = 120
MAX_OUTPUT = 20_000
MAX_DIFF = 60_000

# 告诉 Codex：只读沙箱里要改东西就请求批准，不要直接放弃（不说的话它会回一句「我只有只读权限」就停了）
ASK_NOTE = (
    "（艾拉的说明：你在只读沙箱里运行，这是故意的。要改文件或者运行会写东西的命令时，照常去做，"
    "需要权限就发起批准请求，用户会在艾拉里同意或拒绝；不要因为只读就放弃。用中文回答。）\n\n"
)

# ACP 的停止原因 → 艾拉的一轮怎么结束（fold.ts 的 endText）
STOP = {"end_turn": "done", "max_tokens": "max_steps", "max_turn_requests": "max_steps", "refusal": "error",
        "cancelled": "interrupted"}

# ACP 的工具种类 → 艾拉的工具名（任务页按名字画卡片、权限按种类问）
TOOL_NAMES = {"read": "read_file", "edit": "edit_file", "delete": "edit_file", "move": "edit_file",
              "search": "search", "execute": "run_command", "fetch": "web_fetch"}
POLICY_KINDS = {"read": "read", "search": "read", "fetch": "web", "edit": "edit", "delete": "edit", "move": "edit",
                "execute": "command"}


class _Bridge:
    """ACP 客户端的回调：它说的话、做的事交给 ExternalAgent"""

    def __init__(self, agent: "ExternalAgent") -> None:
        self.agent = agent

    async def session_update(self, session_id: str, update: Any, **kw: Any) -> None:
        self.agent.on_update(update)

    async def request_permission(self, session_id: str, tool_call: Any, options: list[Any], **kw: Any) -> Any:
        return await self.agent.on_permission(tool_call, options)

    # 不提供读写文件、终端：它用自己的工具（我们没在 initialize 里声明这些能力）
    async def read_text_file(self, *a: Any, **kw: Any) -> Any:
        raise acp.RequestError.method_not_found("fs/read_text_file")

    async def write_text_file(self, *a: Any, **kw: Any) -> Any:
        raise acp.RequestError.method_not_found("fs/write_text_file")

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return {}

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        return None


class ExternalAgent:
    """和 loop.Agent 一样给 runner 用：session、policy、turn()、repair()"""

    tools = None
    model = None
    deny_hint = ""

    def __init__(self, session: Session, engine_id: str, policy: Policy, confirms: Confirmations | None,
                 data_dir: Path, spawn: Callable[..., Any] = acp.spawn_agent_process,
                 launcher: Callable[[str, Path], tuple[str, list[str], dict[str, str]]] = launch) -> None:
        if engine_id not in ENGINES:
            raise LocalAgentError(f"不认识的 Agent：{engine_id}")
        self.session = session
        self.engine = ENGINES[engine_id]
        self.policy = policy
        self.confirms = confirms
        self.data_dir = data_dir
        self._spawn = spawn
        self._launch = launcher
        self._stack: AsyncExitStack | None = None
        self._conn: Any = None
        self._sid = ""
        self._fresh = False  # 新开的 ACP 会话：第一句话前面加说明
        self._loading = False
        self._queue: asyncio.Queue[Event] | None = None
        self._text = ""
        self._tools: dict[str, dict[str, Any]] = {}
        self._rejected = ""
        self._said = ""
        self._system_error = False
        self._old: set[str] = set()

    @property
    def root(self) -> Path:
        return self.session.workdir if self.session.workdir.is_dir() else self.session.cwd

    # ---------- 一轮 ----------

    async def turn(self, text: str, extra: dict[str, Any] | None = None) -> AsyncIterator[Event]:
        yield self.session.append({"type": "user", "text": text, **(extra or {})})
        self._queue = asyncio.Queue()
        self._old.update(self._tools)  # 上一轮（可能被打断）的工具晚到的消息不算这一轮的
        self._text, self._tools, self._rejected = "", {}, ""
        self._said, self._system_error = "", False
        reason, error = "done", ""
        task: asyncio.Future[Any] | None = None
        try:
            await self._ensure()
            prompt = (ASK_NOTE if self._fresh and self.engine.id == "codex" else "") + text
            self._fresh = False
            task = asyncio.ensure_future(self._conn.prompt(session_id=self._sid,
                                                           prompt=[schema.TextContentBlock(type="text", text=prompt)]))
            async for event in self._drain(task):
                yield event
            result = task.result()
            reason = STOP.get(str(result.stop_reason), "done")
            if self._system_error:
                reason, error = "error", _error_text(self.engine.title, self._said)
            if self._rejected:
                reason = self._rejected  # 你没同意它要做的事：和艾拉自己的一样停下等你说
        except asyncio.CancelledError:
            # 打断：让它停下这一轮（进程留着下次用），日志补完整；runner 会把补上的事件发出去
            if task is not None and not task.done():
                await self._cancel(task)
            self._close("interrupted")
            raise
        except LocalAgentError as err:
            reason, error = "error", str(err)
        except acp.RequestError as err:
            reason, error = "error", _friendly(self.engine.title, str(err))
            await self.close()
        except (OSError, EOFError, ConnectionError, TimeoutError) as err:
            reason, error = "error", f"{self.engine.title} 的适配器断开了：{err}"
            await self.close()
        for event in self._close(reason, error):
            yield event

    async def _drain(self, task: "asyncio.Future[Any]") -> AsyncIterator[Event]:
        """它跑这一轮的同时，把回调里攒的事件一个个交出去"""
        assert self._queue is not None
        while True:
            getter = asyncio.ensure_future(self._queue.get())
            done, _ = await asyncio.wait({task, getter}, return_when=asyncio.FIRST_COMPLETED)
            if getter in done:
                yield getter.result()
                continue
            getter.cancel()
            while not self._queue.empty():
                yield self._queue.get_nowait()
            return

    async def _cancel(self, task: "asyncio.Future[Any]") -> None:
        try:
            await self._conn.cancel(session_id=self._sid)
            await asyncio.wait_for(asyncio.shield(task), 5)
        except BaseException:
            task.cancel()

    def _close(self, reason: str, error: str = "") -> list[Event]:
        """一轮结束：把没说完的话、没结束的工具卡片补上，记一行结束"""
        events = []
        flushed = self._flush()
        if flushed:
            events.append(flushed)
        status = reason if reason in ("interrupted", "rejected", "denied", "timeout") else "error"
        for call_id, info in list(self._tools.items()):
            if not info.get("done"):
                info["done"] = True
                events.append(self.session.append({"type": "tool", "id": call_id, "name": info["name"],
                                                   "status": status, "content": "没做完就停了。"}))
        events.append(self.session.append({"type": "turn_end", "reason": reason, **({"error": error} if error else {}),
                                           "cost": None}))
        return events

    def repair(self) -> list[Event]:
        """上次没做完就断了的一轮：补完整，记成被打断"""
        return [self.session.append({"type": "turn_end", "reason": "interrupted", "cost": None})]

    async def close(self) -> None:
        stack, self._stack, self._conn = self._stack, None, None
        if stack is not None:
            try:
                await stack.aclose()
            except BaseException:  # 进程已经没了也算关上了
                pass

    # ---------- 连上适配器、开会话或接着以前的 ----------

    async def _ensure(self) -> None:
        if self._conn is not None:
            return
        node, args, env = self._launch(self.engine.id, self.data_dir)
        stack = AsyncExitStack()
        try:
            conn, _proc = await stack.enter_async_context(self._spawn(lambda _a: _Bridge(self), node, *args, env=env,
                                                                       cwd=str(self.root)))
            init = await asyncio.wait_for(conn.initialize(protocol_version=acp.PROTOCOL_VERSION,
                                                          client_capabilities=schema.ClientCapabilities(),
                                                          client_info=schema.Implementation(name="aila", version="0.3")),
                                          INIT_TIMEOUT)
            stored = next((e.get("id") for e in reversed(self.session.events) if e["type"] == "engine_session"), None)
            can_load = bool(getattr(getattr(init, "agent_capabilities", None), "load_session", False))
            if stored and can_load:
                self._loading = True  # 加载时它会把以前的对话重放一遍：不再记
                try:
                    await asyncio.wait_for(conn.load_session(cwd=str(self.root), session_id=stored, mcp_servers=[]),
                                           SESSION_TIMEOUT)
                finally:
                    self._loading = False
                self._sid = stored
            else:
                res = await asyncio.wait_for(conn.new_session(cwd=str(self.root), mcp_servers=[]), SESSION_TIMEOUT)
                self._sid = res.session_id
                self._fresh = True
                self.session.append({"type": "engine_session", "engine": self.engine.id, "id": self._sid})
                await self._configure(conn, res)
            if stored and can_load:
                await self._configure(conn, None)
        except acp.RequestError as err:
            await stack.aclose()
            raise LocalAgentError(_friendly(self.engine.title, str(err))) from err
        except BaseException:
            await stack.aclose()
            raise
        self._stack, self._conn = stack, conn

    async def _configure(self, conn: Any, session: Any) -> None:
        """Codex：只读沙箱（改东西都来请求批准）、用你在 Codex 里设的模型；Claude Code：每次改动前都问"""
        mode = "read-only" if self.engine.id == "codex" else "default"
        try:
            await conn.set_config_option(config_id="mode", session_id=self._sid, value=mode)
        except acp.RequestError:
            await conn.set_session_mode(session_id=self._sid, mode_id=mode)
        if self.engine.id == "codex" and session is not None:
            wanted = codex_model()
            options = _select_values(session, "model")
            if wanted and wanted in options:
                try:
                    await conn.set_config_option(config_id="model", session_id=self._sid, value=wanted)
                except acp.RequestError:
                    pass

    # ---------- 它说的、做的 ----------

    def on_update(self, update: Any) -> None:
        if self._loading or self._queue is None:
            return
        kind = getattr(update, "session_update", "")
        if kind == "agent_message_chunk":
            text = getattr(getattr(update, "content", None), "text", None)
            if text:
                self._text += text
                self._said = (self._said + text)[-2000:]
                self._queue.put_nowait({"type": "text_delta", "text": text})
        elif kind == "session_info_update":
            # Codex 这一轮出了系统错误（比如模型不能用）：它照样说「结束」，这里记下来按出错算
            meta = getattr(update, "field_meta", None) or {}
            status = ((meta.get("codex") or {}).get("threadStatus") or {}).get("type") if isinstance(meta, dict) else None
            if status == "systemError":
                self._system_error = True
        elif kind == "tool_call":
            self._tool_start(update)
        elif kind == "tool_call_update":
            self._tool_update(update)
        elif kind == "plan":
            todos = [{"content": getattr(e, "content", ""), "status": _todo_status(getattr(e, "status", ""))}
                     for e in getattr(update, "entries", []) or []]
            call_id = f"plan-{len(self.session.events)}"
            self._emit(self._flush([{"id": call_id, "name": "todo_write", "arguments": "{}"}]))
            self._emit(self.session.append({"type": "tool", "id": call_id, "name": "todo_write", "status": "ok",
                                            "content": "待办清单已更新。", "todos": todos}))

    def _emit(self, event: Event | None) -> None:
        if event is not None and self._queue is not None:
            self._queue.put_nowait(event)

    def _flush(self, calls: list[dict[str, Any]] | None = None) -> Event | None:
        """攒着的话（和这一步要调的工具）记成一条 assistant；什么都没有就不记"""
        if not self._text and not calls:
            return None
        text, self._text = self._text, ""
        return self.session.append({"type": "assistant", "text": text, "tool_calls": calls or []})

    def _tool_start(self, update: Any) -> None:
        call_id = str(getattr(update, "tool_call_id", ""))
        if call_id in self._old:
            return
        if not call_id or call_id in self._tools:
            self._tool_update(update)
            return
        kind = str(getattr(update, "kind", "") or "other")
        title = _short(str(getattr(update, "title", "") or ""), self.root)
        name = TOOL_NAMES.get(kind, "run_command" if kind == "execute" else "tool")
        args = _tool_args(name, update, self.root)
        self._tools[call_id] = {"name": name, "kind": kind, "title": title, "full_title": str(getattr(update, "title", "") or ""),
                                "args": args, "output": "", "diffs": []}
        self._emit(self._flush([{"id": call_id, "name": name, "arguments": json.dumps(args, ensure_ascii=False)}]))
        self._emit({"type": "tool_start", "id": call_id, "name": name, "status": title[:120] or self.engine.title})
        self._collect(call_id, update)

    def _tool_update(self, update: Any) -> None:
        call_id = str(getattr(update, "tool_call_id", ""))
        info = self._tools.get(call_id)
        if info is None:
            if call_id not in self._old and getattr(update, "title", None):  # 没见过开头的：当成新的一张卡片
                self._tool_start(update)
            return
        self._collect(call_id, update)
        status = str(getattr(update, "status", "") or "")
        if status in ("completed", "failed") and not info.get("done"):
            info["done"] = True
            self._emit(self.session.append(self._tool_result(call_id, info, ok=status == "completed")))

    def _collect(self, call_id: str, update: Any) -> None:
        """攒工具的输出：内容块里的文字、终端输出（Codex 放在 _meta 里）、改动（diff 块）"""
        info = self._tools[call_id]
        meta = getattr(update, "field_meta", None) or {}
        delta = (meta.get("terminal_output_delta") or {}).get("data") if isinstance(meta, dict) else None
        if delta:
            info["output"] = (info["output"] + delta)[-MAX_OUTPUT:]
        for block in getattr(update, "content", None) or []:
            if getattr(block, "type", "") == "diff":
                info["diffs"].append((str(getattr(block, "path", "")), getattr(block, "old_text", None) or "",
                                      str(getattr(block, "new_text", "") or "")))
            else:
                text = getattr(getattr(block, "content", None), "text", None)
                if text:
                    info["output"] = (info["output"] + text)[-MAX_OUTPUT:]
        exit_code = (meta.get("terminal_exit") or {}).get("exit_code") if isinstance(meta, dict) else None
        if exit_code is not None:
            info["exit_code"] = exit_code

    def _tool_result(self, call_id: str, info: dict[str, Any], ok: bool) -> Event:
        event: Event = {"type": "tool", "id": call_id, "name": info["name"], "status": "ok" if ok else "error"}
        if info["diffs"]:
            path, diff, added, removed = _unified(info["diffs"], self.root)
            event.update({"content": f"改好了 {path}（+{added} −{removed}）\n{diff}"[:MAX_DIFF], "diff": diff[:MAX_DIFF],
                          "file": path})
        elif info["name"] == "run_command":
            code = info.get("exit_code")
            head = f"退出码 {code}" if code is not None else ("做完了" if ok else "没成功")
            event.update({"content": f"{head}\n{info['output'].strip()}", "exit_code": code})
        else:
            event["content"] = (info["output"].strip() or info["title"] or ("做完了" if ok else "没成功"))[:MAX_OUTPUT]
        return event

    # ---------- 批准请求：按艾拉的规则 ----------

    async def on_permission(self, tool_call: Any, options: list[Any]) -> Any:
        allow = _option(options, "allow_once") or _option(options, "allow_always")
        reject = _option(options, "reject_once") or _option(options, "reject_always")
        call_id = str(getattr(tool_call, "tool_call_id", ""))
        info = self._tools.get(call_id)
        kind_acp = str(getattr(tool_call, "kind", "") or (info or {}).get("kind") or "other")
        # 判断危险用完整的原文（多行命令的第二行也算）；给你看的标题才用缩短的
        full_title = str(getattr(tool_call, "title", "") or (info or {}).get("full_title") or "")
        title = _short(full_title, self.root)
        if info is None and call_id:
            self._tool_start(tool_call)
            info = self._tools.get(call_id)
        if info is not None:
            self._collect(call_id, tool_call)
        kind = POLICY_KINDS.get(kind_acp, "command")
        command = _command(tool_call, full_title) if kind == "command" else ""
        if kind == "command" and not command and info is not None:
            command = str(info["args"].get("command") or "")
        decision = self.policy.decide(kind, command)
        if decision.verdict == "allow" and allow is not None:
            return _choose(allow)
        if decision.verdict == "deny" or self.confirms is None or allow is None:
            why = decision.reason or "这个操作需要你确认，但现在没法问你。"
            self._emit(self.session.append({"type": "note", "text": f"没让 {self.engine.title} 做：{why}"}))
            self._rejected = "denied"
            return _choose(reject) if reject is not None else schema.RequestPermissionResponse(
                outcome=schema.DeniedOutcome(outcome="cancelled"))
        pending = self.confirms.open()
        try:
            self._emit(self.session.append({
                "type": "confirm", "id": pending.id, "call_id": call_id, "tool": (info or {}).get("name", "run_command"),
                "kind": kind, "action": _action(kind, title, info, self.root),
                "detail": _detail(kind, command, title, info, tool_call, self.root),
                "reason": decision.reason, "can_remember": decision.can_remember,
            }))
            result = await self.confirms.wait(pending)
        finally:
            self.confirms.close(pending)
        remember = pending.remember and decision.can_remember
        self._emit(self.session.append({"type": "confirm_end", "id": pending.id, "result": result, "remember": remember}))
        if result == "approved":
            if remember:
                self.policy.remember(kind, command)
            return _choose(allow)
        self._rejected = result
        return _choose(reject) if reject is not None else schema.RequestPermissionResponse(
            outcome=schema.DeniedOutcome(outcome="cancelled"))


# ---------- 小工具 ----------


def _option(options: list[Any], kind: str) -> Any:
    return next((o for o in options if str(getattr(o, "kind", "")) == kind), None)


def _choose(option: Any) -> Any:
    return schema.RequestPermissionResponse(outcome=schema.AllowedOutcome(option_id=option.option_id, outcome="selected"))


def _todo_status(status: str) -> str:
    return {"in_progress": "in_progress", "completed": "completed"}.get(str(status), "pending")


def _short(title: str, root: Path) -> str:
    """卡片标题里的项目绝对路径去掉（「Read file 'C:\\…\\proj\\calc.py'」→「Read file 'calc.py'」），多行的只要第一行"""
    first = title.strip().splitlines()[0] if title.strip() else ""
    for prefix in {str(root) + "\\", str(root) + "/", root.as_posix() + "/"}:
        first = first.replace(prefix, "")
    return first


def _rel(path: str, root: Path) -> str:
    try:
        return Path(path).resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return path


def _tool_args(name: str, update: Any, root: Path) -> dict[str, Any]:
    title = str(getattr(update, "title", "") or "")
    locations = [str(getattr(loc, "path", "")) for loc in getattr(update, "locations", None) or []]
    first = _rel(locations[0], root) if locations else ""
    if name == "run_command":
        return {"command": _command(update, title), "description": title[:80]}
    if name in ("read_file", "edit_file"):
        return {"path": first or title}
    if name == "search":
        return {"pattern": title}
    if name == "web_fetch":
        return {"url": title}
    return {"title": title}


def _command(tool_call: Any, title: str) -> str:
    """要跑的命令原文：优先它给的参数（raw_input），没有就用标题"""
    raw = getattr(tool_call, "raw_input", None)
    if isinstance(raw, dict):
        cmd = raw.get("command") or raw.get("cmd")
        if isinstance(cmd, list):
            # Codex 有时给 ["powershell.exe", "-Command", "真正的命令"]：取最后一段
            return str(cmd[-1]) if len(cmd) >= 3 and str(cmd[-2]).lower() in ("-command", "-c", "/c") else " ".join(map(str, cmd))
        if isinstance(cmd, str):
            return cmd
    return title


def _unified(diffs: list[tuple[str, str, str]], root: Path) -> tuple[str, str, int, int]:
    """它报上来的改动（原文、新文）由这里生成 diff：给你看的不用它的描述"""
    parts, added, removed = [], 0, 0
    for path, old, new in diffs:
        rel = _rel(path, root)
        lines = list(difflib.unified_diff(old.splitlines(), new.splitlines(), f"a/{rel}", f"b/{rel}", lineterm=""))
        added += sum(1 for line in lines if line.startswith("+") and not line.startswith("+++"))
        removed += sum(1 for line in lines if line.startswith("-") and not line.startswith("---"))
        parts.append("\n".join(lines))
    first = _rel(diffs[0][0], root) if diffs else ""
    return first, "\n".join(p for p in parts if p), added, removed


def _action(kind: str, title: str, info: dict[str, Any] | None, root: Path) -> str:
    if kind == "edit":
        path = (info or {}).get("args", {}).get("path") or title
        return f"改 {path}"
    if kind == "command":
        return f"运行命令 {title[:80]}" if title else "运行一条命令"
    return title[:80] or "做一个操作"


def _detail(kind: str, command: str, title: str, info: dict[str, Any] | None, tool_call: Any, root: Path) -> str:
    """问你时给你看的内容：改文件是代码算出来的 diff，命令是命令原文"""
    if kind == "edit":
        diffs = (info or {}).get("diffs") or []
        if diffs:
            return _unified(diffs, root)[1][:MAX_DIFF] or "（内容没有变化）"
        locations = [_rel(str(getattr(loc, "path", "")), root) for loc in getattr(tool_call, "locations", None) or []]
        return "要改的文件：\n" + "\n".join(locations or [title])
    if kind == "command":
        return command
    return title


def _select_values(session: Any, option_id: str) -> list[str]:
    for opt in getattr(session, "config_options", None) or []:
        root = getattr(opt, "root", opt)
        if getattr(root, "id", "") == option_id:
            values = []
            for o in getattr(root, "options", None) or []:
                o = getattr(o, "root", o)
                if hasattr(o, "value"):
                    values.append(str(o.value))
                for sub in getattr(o, "options", None) or []:  # 分组的选项
                    values.append(str(getattr(sub, "value", "")))
            return values
    return []


def codex_model(home: Path | None = None) -> str:
    """你在 Codex 里设的模型（config.toml 的 model 一行）：适配器默认挑的模型 ChatGPT 账号不一定能用"""
    base = home or Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    try:
        for line in (base / "config.toml").read_text(encoding="utf-8").splitlines():
            m = re.match(r'\s*model\s*=\s*"([^"]+)"', line)
            if m:
                return m.group(1)
            if line.strip().startswith("["):
                break  # 只看最前面的全局设置
    except OSError:
        pass
    return ""


def _error_text(title: str, said: str) -> str:
    """Codex 把服务端的错误原样（JSON）说出来：挑出里面的 message"""
    m = re.search(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)"', said)
    detail = m.group(1) if m else said.strip().splitlines()[-1] if said.strip() else "没说原因"
    if "not supported" in detail and "model" in detail:
        return f"{title} 用的模型你的账号不能用：{detail[:200]}。在 {title} 的设置里换一个模型再试。"
    return f"{title} 出错了：{detail[:300]}"


def _friendly(title: str, message: str) -> str:
    if "auth" in message.lower():
        engine = next((e for e in ENGINES.values() if e.title == title), None)
        return f"{title} 还没登录。" + (engine.login_hint if engine else "")
    return f"{title} 出错了：{message[:300]}"


__all__ = ["ASK_NOTE", "ExternalAgent", "codex_model"]
