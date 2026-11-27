"""对话的「大脑」换成本机的 Codex / Claude Code（通过 ACP 适配器，用你自己的账号）。

对话的流程一点不变（agent.run_agent）：人格设定、记忆（<背景>）、表情动作标签、[say] 念出来的话、工具、确认都照旧。
这里只是实现 ChatModel 的 stream()，把「想下一句」交给本机 Agent：

- 人格：开 ACP 会话时把艾拉的系统提示词交给它（Claude Code 整个换掉它原来的编程助手设定，Codex 只能接在后面）
- 记忆：<背景> 已经放在最新一句话前面，原样交过去
- 连续对话：会话留着，下一句只发新的那句；对不上（艾拉重启了、换了对话、技能包开关变了）就开新会话，
  把最近的对话当前情提要一起发过去
- 艾拉的工具（搜网页、提醒、打开页面……）：用 MCP 挂给它（POST /chat/bridge，只认这个大脑的令牌）。
  它调用时，stream() 把调用交回 run_agent（就像模型说「我要调这个工具」），run_agent 照常确认、执行，
  结果在下一次 stream() 交回给它，它接着往下说
- 它自己的读文件、跑命令这些：对话里用不上，开在一个空文件夹里，要改东西、跑命令的请求一律不批
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, AsyncIterator, Callable

import acp
from acp import schema

from .llm import Message, ToolCall
from .runtime.external import _options_of
from .runtime.localagents import ENGINES, launch

log = logging.getLogger("aila.brain")

SERVER_NAME = "aila_chat"
TOOL_WAIT = 55  # 秒：它等一个工具结果最多这么久（它那边的工具调用超时一般是 60 秒）
CONTEXT_TURNS = 12  # 开新会话时带上最近几句当前情提要
INIT_TIMEOUT = 60
BACKGROUND = re.compile(r"^<背景>.*?</背景>\s*", re.S)


class BrainError(Exception):
    """说给用户听的（chat 路由按出错处理，她会说一句）"""


class _Bridge:
    def __init__(self, brain: "AgentBrain") -> None:
        self.brain = brain

    async def session_update(self, session_id: str, update: Any, **kw: Any) -> None:
        self.brain.on_update(update)

    async def request_permission(self, session_id: str, tool_call: Any, options: list[Any], **kw: Any) -> Any:
        return self.brain.on_permission(tool_call, options)

    async def read_text_file(self, *a: Any, **kw: Any) -> Any:
        raise acp.RequestError.method_not_found("fs/read_text_file")

    async def write_text_file(self, *a: Any, **kw: Any) -> Any:
        raise acp.RequestError.method_not_found("fs/write_text_file")

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return {}

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        return None


class AgentBrain:
    """ChatModel：stream(messages, tools)。一个大脑同时只跟一段对话（桌面上的她一次只说一件事）"""

    def __init__(self, engine_id: str, data_dir: Path, base_url: str | None,
                 spawn: Callable[..., Any] = acp.spawn_agent_process,
                 launcher: Callable[[str, Path], tuple[str, list[str], dict[str, str]]] = launch,
                 model: Callable[[], str] = lambda: "") -> None:
        if engine_id not in ENGINES:
            raise BrainError(f"不认识的 Agent：{engine_id}")
        self.engine = ENGINES[engine_id]
        self.name = self.engine.title
        self.data_dir = data_dir
        self.base_url = base_url
        self.token = secrets.token_urlsafe(32)
        self._spawn, self._launch = spawn, launcher
        self._model = model  # 设置里选的模型（空 = 它自己的默认）
        self.options: list[dict[str, Any]] = []  # 它给的模型（开会话时拿到）
        self._stack: AsyncExitStack | None = None
        self._conn: Any = None
        self._sid = ""
        self._signature = ""  # 系统提示词 + 工具：变了就开新会话
        self._said_users: list[str] = []  # 已经发给它的你说的话（不带 <背景>），对得上才接着用这个会话
        self._specs: list[dict[str, Any]] = []  # 这次给它的艾拉的工具（MCP 格式）
        self._queue: asyncio.Queue[Any] | None = None
        self._turn: asyncio.Future[Any] | None = None
        self._pending: dict[str, asyncio.Future[str]] = {}
        self._titles: dict[str, str] = {}  # 它的工具调用编号 → 名字（批准请求里可能不带名字）
        self._n = 0
        self._lock = asyncio.Lock()

    # ---------- ChatModel ----------

    async def stream(self, messages: list[Message], tools: list[dict[str, Any]] | None = None) -> AsyncIterator[str | ToolCall]:
        waiting = [m for m in messages if m.get("role") == "tool" and m.get("tool_call_id") in self._pending]
        if self._turn is not None and not self._turn.done() and waiting:
            for m in waiting:  # 上次停在工具上：run_agent 执行完了，结果交回给它
                fut = self._pending.pop(str(m["tool_call_id"]))
                if not fut.done():
                    fut.set_result(str(m.get("content", "")))
        else:
            await self._start(messages, tools or [])
        try:
            async for item in self._drain():
                yield item
        except (asyncio.CancelledError, GeneratorExit):
            await self._cancel()
            raise

    async def _drain(self) -> AsyncIterator[str | ToolCall]:
        assert self._queue is not None
        while True:
            item = await self._queue.get()
            if isinstance(item, str):
                yield item
            elif isinstance(item, ToolCall):
                yield item
                return  # run_agent 去执行，执行完再叫 stream()
            elif isinstance(item, BaseException):
                raise item
            else:  # 这一轮说完了
                return

    async def _start(self, messages: list[Message], tools: list[dict[str, Any]]) -> None:
        async with self._lock:
            system = next((str(m["content"]) for m in messages if m.get("role") == "system"), "")
            specs = [_mcp_spec(t) for t in tools if t.get("type") == "function"]
            signature = json.dumps([system, [s["name"] for s in specs]], ensure_ascii=False)
            users = [_strip(str(m.get("content", ""))) for m in messages if m.get("role") == "user"]
            last = str(next(m["content"] for m in reversed(messages) if m.get("role") == "user"))
            fresh = (self._conn is None or signature != self._signature
                     or users[:-1][-3:] != self._said_users[-3:] or len(users) <= 1 and bool(self._said_users))
            self._specs = specs
            if fresh:
                await self._open(system, signature)
                prompt = _recap(messages) + last
            else:
                await self._apply_model()  # 聊着聊着在设置里换了模型
                prompt = last
            self._said_users = users
            self._queue = asyncio.Queue()
            self._pending.clear()
            self._turn = asyncio.ensure_future(self._prompt(prompt))

    async def _prompt(self, text: str) -> None:
        assert self._queue is not None
        try:
            res = await self._conn.prompt(session_id=self._sid, prompt=[schema.TextContentBlock(type="text", text=text)])
            self._queue.put_nowait(("done", str(res.stop_reason)))
        except acp.RequestError as err:
            self._queue.put_nowait(BrainError(_friendly(self.name, str(err))))
            await self.close()
        except Exception as err:  # 适配器没了
            self._queue.put_nowait(BrainError(f"{self.name} 断开了：{err}"))
            await self.close()

    async def _cancel(self) -> None:
        """对话被取消（你打断她、关了对话框）：让它也停下"""
        for fut in self._pending.values():
            if not fut.done():
                fut.set_result("用户打断了，这个工具没有执行。")
        self._pending.clear()
        if self._turn is not None and not self._turn.done() and self._conn is not None:
            try:
                await self._conn.cancel(session_id=self._sid)
            except Exception:
                pass

    # ---------- 连上它、开会话 ----------

    async def _open(self, system: str, signature: str) -> None:
        if self._conn is None:
            node, args, env = self._launch(self.engine.id, self.data_dir)
            home = self.data_dir / "chat-brain"  # 空文件夹：对话用不上它读写文件
            home.mkdir(parents=True, exist_ok=True)
            stack = AsyncExitStack()
            try:
                conn, _ = await stack.enter_async_context(self._spawn(lambda _a: _Bridge(self), node, *args, env=env, cwd=str(home)))
                await asyncio.wait_for(conn.initialize(protocol_version=acp.PROTOCOL_VERSION,
                                                       client_capabilities=schema.ClientCapabilities(),
                                                       client_info=schema.Implementation(name="aila", version="0.3")), INIT_TIMEOUT)
            except BaseException:
                await stack.aclose()
                raise
            self._stack, self._conn = stack, conn
        mode = "override" if self.engine.id == "claude" else "append"  # Codex 只能接在它原来的后面
        servers = []
        if self.base_url:
            servers.append(schema.HttpMcpServer(type="http", name=SERVER_NAME, url=f"{self.base_url}/chat/bridge",
                                                headers=[schema.HttpHeader(name="Authorization", value=f"Bearer {self.token}")]))
        try:
            res = await asyncio.wait_for(self._conn.new_session(
                cwd=str(self.data_dir / "chat-brain"), mcp_servers=servers,
                system_prompt=schema.SystemPrompt(mode=mode, content=[schema.TextContentBlock(type="text", text=system)]) if system else None),
                INIT_TIMEOUT)
        except acp.RequestError as err:
            await self.close()
            raise BrainError(_friendly(self.name, str(err))) from err
        self._sid, self._signature = res.session_id, signature
        self.options = [o for o in _options_of(getattr(res, "config_options", None)) if o["category"] == "model"]
        await self._apply_model()
        # 只读：Codex 开在只读沙箱，Claude Code 每次改动前都问（问了也不批）
        try:
            await self._conn.set_config_option(config_id="mode", session_id=self._sid,
                                               value="read-only" if self.engine.id == "codex" else "default")
        except acp.RequestError:
            pass

    async def _apply_model(self) -> None:
        wanted = self._model()
        for o in self.options:
            if wanted and wanted != o["current"] and wanted in [c["value"] for c in o["choices"]]:
                try:
                    await self._conn.set_config_option(config_id=o["id"], session_id=self._sid, value=wanted)
                    o["current"] = wanted
                except acp.RequestError:
                    pass  # 它不认这个模型了：用它现在的

    async def models(self) -> list[dict[str, Any]]:
        """它有哪些模型（设置页的下拉）：还没开过会话就先开一个问它"""
        async with self._lock:
            if not self.options:
                await self._open("", "")
        wanted = self._model()
        return [{**o, "current": wanted or o["current"]} for o in self.options]

    async def close(self) -> None:
        stack, self._stack, self._conn = self._stack, None, None
        if stack is not None:
            try:
                await stack.aclose()
            except BaseException:
                pass

    # ---------- 它说的话、它要的权限、它调艾拉的工具 ----------

    def on_update(self, update: Any) -> None:
        kind = getattr(update, "session_update", "")
        if kind in ("tool_call", "tool_call_update") and getattr(update, "title", None):
            # 记下工具的名字：Codex 请求批准时常常不带标题，只带编号
            self._titles[str(getattr(update, "tool_call_id", ""))] = str(update.title)
            return
        if self._queue is None or kind != "agent_message_chunk":
            return
        text = getattr(getattr(update, "content", None), "text", None)
        if text:
            self._queue.put_nowait(text)

    def on_permission(self, tool_call: Any, options: list[Any]) -> Any:
        """对话里不让它改东西、跑命令；只有调艾拉给它的工具放行（工具自己要确认的，run_agent 会问你）"""
        title = str(getattr(tool_call, "title", "") or self._titles.get(str(getattr(tool_call, "tool_call_id", "")), ""))
        names = {s["name"] for s in self._specs}
        own = any(title.startswith(p) and title[len(p):] in names for p in (f"mcp.{SERVER_NAME}.", f"mcp__{SERVER_NAME}__"))
        raw = getattr(tool_call, "raw_input", None)
        command = isinstance(raw, dict) and (raw.get("command") or raw.get("cmd"))
        wanted = "allow_once" if own and not command else "reject_once"
        if wanted == "reject_once":
            log.info("对话里没批 %s 的请求：%r（%s）", self.name, title[:120], getattr(tool_call, "kind", ""))
        option = next((o for o in options if str(o.kind) == wanted), None)
        if option is None:
            return schema.RequestPermissionResponse(outcome=schema.DeniedOutcome(outcome="cancelled"))
        return schema.RequestPermissionResponse(outcome=schema.AllowedOutcome(option_id=option.option_id, outcome="selected"))

    async def tool_call(self, name: str, args: dict[str, Any]) -> str:
        """它通过 MCP 调艾拉的工具：交给 run_agent（确认、执行），等结果"""
        if self._queue is None or self._turn is None or self._turn.done():
            return "现在不在对话里，这个工具没有执行。"
        if name not in {s["name"] for s in self._specs}:
            return f"没有叫 {name} 的工具。"
        self._n += 1
        call_id = f"brain-{self._n}"
        fut: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._pending[call_id] = fut
        self._queue.put_nowait(ToolCall(call_id, name, json.dumps(args, ensure_ascii=False)))
        try:
            return await asyncio.wait_for(asyncio.shield(fut), TOOL_WAIT)
        except TimeoutError:
            return "用户还没回答要不要做，先当没做：简单说一句就好。"

    @property
    def specs(self) -> list[dict[str, Any]]:
        return self._specs


def _mcp_spec(tool: dict[str, Any]) -> dict[str, Any]:
    fn = tool["function"]
    return {"name": fn["name"], "description": fn.get("description", ""),
            "inputSchema": fn.get("parameters") or {"type": "object", "properties": {}}}


def _strip(text: str) -> str:
    return BACKGROUND.sub("", text).strip()


def _recap(messages: list[Message]) -> str:
    """开新会话时：最近的对话当前情提要（它之前没听过的）"""
    turns = [m for m in messages if m.get("role") in ("user", "assistant") and str(m.get("content", "")).strip()][:-1]
    turns = turns[-CONTEXT_TURNS:]
    if not turns:
        return ""
    lines = [("对方：" if m["role"] == "user" else "你：") + _strip(str(m["content"]))[:600] for m in turns]
    return "（前面聊过的，接着聊就好，不用复述）\n" + "\n".join(lines) + "\n\n"


def _friendly(title: str, message: str) -> str:
    if "auth" in message.lower():
        return f"{title} 还没登录。" + ENGINES[next(k for k, e in ENGINES.items() if e.title == title)].login_hint
    return f"{title} 出错了：{message[:200]}"


__all__ = ["AgentBrain", "BrainError", "SERVER_NAME"]
