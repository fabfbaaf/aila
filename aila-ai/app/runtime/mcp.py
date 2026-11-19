"""MCP（Model Context Protocol）：让她用外面现成的工具服务（数据库、浏览器、GitHub、各种 API）。

为什么这样设计：
- 自己写一个最小的 stdio 客户端（JSON-RPC 2.0，一行一条消息），不加依赖：初始化、列工具、调工具，
  再加上回应服务端的 ping。远程（HTTP）服务暂不支持，配置里写了 url 的会标成「不支持」
- 配置和对话那边（app/tools/mcp.py，每个服务是一个技能包）共用同一个 aila-ai/mcp.json：加一次两边都能用。
  servers / mcpServers 两种写法都认（MCP 服务说明里 Claude Desktop 的配置能直接贴进来），也认整个服务的
  readOnly、显示名 name；写回时保留这里不用的字段。只有你能加服务（设置页、手改文件），她没有加服务的工具
- 两边各自连服务（对话那边开机连一次；这边能在设置页加、改、重启）；对话那边要重启 AI 服务才看到新加的
- 每个服务是一个子进程：环境变量里去掉艾拉自己的密钥（和她跑命令一样），再加上配置里给这个服务的；
  服务的密钥（配置里的 env）不回传给界面，也不进模型上下文
- 工具叫 mcp__服务名__工具名；只在一轮开始前对齐（runner 负责），一轮里工具列表不变，前缀缓存稳定
- 权限（permissions.py）：服务标了只读（readOnlyHint）的工具算读，不用问；其他的只读模式不让用，
  另外两种模式都要问（这些工具在项目以外做事，「工作区内自动」管不到），可以选「这个工具以后都不用问」，
  记在配置的 alwaysAllow 里（和 Cline 的写法一样）
- 工具返回的内容只当数据，有长度上限
"""

import asyncio
import contextlib
import hashlib
import json
import logging
import re
import shutil
import subprocess
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .shell import WIN, clean_env, kill_tree

log = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-06-18"
KNOWN_FIELDS = {"command", "args", "env", "cwd", "url", "disabled", "alwaysAllow", "timeout", "readOnly"}
START_TIMEOUT = 60.0  # npx 第一次要下载，慢
CALL_TIMEOUT = 120.0
RESULT_CHARS = 20000
DESCRIPTION_CHARS = 1000
LOG_LINES = 40
READ_LIMIT = 16 * 1024 * 1024  # 一条消息最长
SERVER_NAME = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
TOOL_PREFIX = "mcp__"


class McpError(Exception):
    """配置不对、服务起不来、调用失败"""


# ---------- 配置 ----------


@dataclass
class ServerConfig:
    name: str
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str = ""
    url: str = ""
    disabled: bool = False
    always_allow: list[str] = field(default_factory=list)
    timeout: float = CALL_TIMEOUT
    # 整个服务标了只读（和对话那边一样）：所有工具都算读
    read_only: bool = False
    # 这里不用、但要原样写回的字段（比如对话那边的显示名 name）
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def parse(cls, name: str, raw: Any) -> "ServerConfig":
        if not SERVER_NAME.fullmatch(name or ""):
            raise McpError(f"服务名「{name}」不行：只能用字母、数字、下划线、减号，最多 32 个字。")
        if not isinstance(raw, dict):
            raise McpError(f"{name} 的配置要是一个对象。")
        command = raw.get("command", "")
        url = raw.get("url", "")
        if not isinstance(command, str) or not isinstance(url, str) or not (command.strip() or url.strip()):
            raise McpError(f"{name} 要有 command（本机启动的服务）。")
        args = raw.get("args", [])
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise McpError(f"{name} 的 args 要是字符串列表。")
        env = raw.get("env", {})
        if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
            raise McpError(f"{name} 的 env 要是「名字: 字符串」。")
        allow = raw.get("alwaysAllow", [])
        timeout = raw.get("timeout", CALL_TIMEOUT)
        return cls(
            name=name,
            command=command.strip(),
            args=list(args),
            env=dict(env),
            cwd=str(raw.get("cwd", "") or ""),
            url=url.strip(),
            disabled=bool(raw.get("disabled", False)),
            always_allow=[a for a in allow if isinstance(a, str)] if isinstance(allow, list) else [],
            timeout=float(timeout) if isinstance(timeout, (int, float)) and not isinstance(timeout, bool) and 1 <= timeout <= 600 else CALL_TIMEOUT,
            read_only=raw.get("readOnly") is True,
            extra={k: v for k, v in raw.items() if k not in KNOWN_FIELDS},
        )

    def to_raw(self) -> dict[str, Any]:
        raw: dict[str, Any] = dict(self.extra)
        raw.update({"url": self.url} if self.url and not self.command else {"command": self.command, "args": self.args})
        if self.read_only:
            raw["readOnly"] = True
        if self.env:
            raw["env"] = self.env
        if self.cwd:
            raw["cwd"] = self.cwd
        if self.disabled:
            raw["disabled"] = True
        if self.always_allow:
            raw["alwaysAllow"] = self.always_allow
        if self.timeout != CALL_TIMEOUT:
            raw["timeout"] = self.timeout
        return raw

    def command_line(self) -> str:
        """给你看的启动命令（由代码拼出来；不含 env 的值）"""
        if not self.command:
            return self.url
        return " ".join([self.command, *(f'"{a}"' if " " in a else a for a in self.args)])


def parse_import(text: str) -> dict[str, ServerConfig]:
    """贴进来的配置：{"mcpServers": {...}}、{"名字": {...}} 两种都认"""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as err:
        raise McpError(f"不是合法的 JSON：{err.msg}（第 {err.lineno} 行）") from err
    if isinstance(data, dict):
        for key in ("mcpServers", "servers"):
            if isinstance(data.get(key), dict):
                data = data[key]
                break
    if not isinstance(data, dict) or not data:
        raise McpError("没找到服务配置：要像 {\"mcpServers\": {\"名字\": {\"command\": \"npx\", \"args\": [...]}}} 这样。")
    return {name: ServerConfig.parse(name, raw) for name, raw in data.items()}


# ---------- 工具 ----------


@dataclass
class McpTool:
    server: str
    name: str
    description: str
    schema: dict[str, Any]
    read_only: bool = False

    @property
    def full_name(self) -> str:
        return tool_name(self.server, self.name)


def tool_name(server: str, tool: str) -> str:
    """给模型的函数名：只能用字母数字下划线减号，最长 64"""
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", tool) or "tool"
    full = f"{TOOL_PREFIX}{server}__{safe}"
    if len(full) <= 64:
        return full
    digest = hashlib.sha1(tool.encode("utf-8")).hexdigest()[:8]
    return f"{full[:55]}_{digest}"


def render_result(result: Any) -> tuple[str, bool]:
    """tools/call 的结果 → （给模型的文字，是不是出错）"""
    if not isinstance(result, dict):
        return "（工具没有返回内容）", False
    parts: list[str] = []
    for item in result.get("content") or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "text":
            parts.append(str(item.get("text", "")))
        elif kind == "image":
            parts.append(f"[图片 {item.get('mimeType', '')}，这里显示不了]")
        elif kind == "audio":
            parts.append(f"[音频 {item.get('mimeType', '')}]")
        elif kind == "resource":
            res = item.get("resource") or {}
            parts.append(str(res.get("text")) if isinstance(res, dict) and res.get("text") else f"[资源 {res.get('uri', '') if isinstance(res, dict) else ''}]")
        elif kind == "resource_link":
            parts.append(f"[资源 {item.get('uri', '')}]")
    if not parts and result.get("structuredContent") is not None:
        parts.append(json.dumps(result["structuredContent"], ensure_ascii=False))
    text = "\n".join(p for p in parts if p).strip() or "（工具没有返回内容）"
    if len(text) > RESULT_CHARS:
        text = text[:RESULT_CHARS] + f"\n……（太长了，只给前 {RESULT_CHARS} 个字）"
    return text, bool(result.get("isError"))


# ---------- 一个服务（stdio） ----------


class StdioClient:
    """一个 MCP 服务子进程：一行一条 JSON-RPC 消息"""

    def __init__(self, config: ServerConfig, on_tools_changed: Callable[[], None] | None = None) -> None:
        self.config = config
        self.on_tools_changed = on_tools_changed
        self.proc: asyncio.subprocess.Process | None = None
        self.server_info: dict[str, Any] = {}
        self.log: deque[str] = deque(maxlen=LOG_LINES)
        self._next = 0
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._tasks: list[asyncio.Task[None]] = []
        self._closed = False

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None and not self._closed

    async def start(self, timeout: float = START_TIMEOUT) -> None:
        exe = shutil.which(self.config.command)
        if exe is None:
            hint = "装好 Node.js 后再试" if self.config.command in ("npx", "node", "npm") else "检查命令名和 PATH"
            raise McpError(f"找不到命令 {self.config.command}：{hint}。")
        env = clean_env()
        env.update(self.config.env)
        cwd = self.config.cwd or str(Path.home())
        kwargs: dict[str, Any] = {}
        if WIN:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True
        try:
            self.proc = await asyncio.create_subprocess_exec(
                exe, *self.config.args, cwd=cwd, env=env, limit=READ_LIMIT,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **kwargs,
            )
        except OSError as err:
            raise McpError(f"启动 {self.config.command} 失败：{err}") from err
        self._tasks = [asyncio.create_task(self._read()), asyncio.create_task(self._drain_stderr())]
        try:
            result = await self.request("initialize", {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "aila", "title": "艾拉", "version": "0.4.0"},
            }, timeout)
            self.server_info = result.get("serverInfo", {}) if isinstance(result, dict) else {}
            await self.notify("notifications/initialized")
        except BaseException:
            await self.close()
            raise

    async def request(self, method: str, params: dict[str, Any] | None = None, timeout: float = CALL_TIMEOUT) -> Any:
        if not self.alive:
            raise McpError(f"{self.config.name} 没在运行。")
        self._next += 1
        msg_id = self._next
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        try:
            await self._send({"jsonrpc": "2.0", "id": msg_id, "method": method, **({"params": params} if params is not None else {})})
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError as err:
            with contextlib.suppress(McpError):
                await self.notify("notifications/cancelled", {"requestId": msg_id, "reason": "timeout"})
            raise McpError(f"{self.config.name} 超过 {timeout:.0f} 秒没回应。") from err
        finally:
            self._pending.pop(msg_id, None)

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, **({"params": params} if params is not None else {})})

    async def _send(self, message: dict[str, Any]) -> None:
        if not self.proc or not self.proc.stdin or not self.alive:
            raise McpError(f"{self.config.name} 没在运行。")
        try:
            self.proc.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
            await self.proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as err:
            raise McpError(f"{self.config.name} 已经退出了。") from err

    async def _read(self) -> None:
        assert self.proc and self.proc.stdout
        try:
            while True:
                try:
                    line = await self.proc.stdout.readline()
                except ValueError:  # 一行太长
                    self.log.append("（收到一条太长的消息，丢掉了）")
                    continue
                if not line:
                    break
                try:
                    message = json.loads(line.decode("utf-8", "replace"))
                except json.JSONDecodeError:
                    self.log.append(line.decode("utf-8", "replace").rstrip()[:300])  # 有的服务往 stdout 打日志
                    continue
                if isinstance(message, dict):
                    await self._dispatch(message)
        finally:
            self._fail_pending(f"{self.config.name} 已经退出了。")

    async def _dispatch(self, message: dict[str, Any]) -> None:
        if "method" not in message:
            future = self._pending.get(message.get("id")) if isinstance(message.get("id"), int) else None
            if future is None or future.done():
                return
            if "error" in message:
                err = message["error"] if isinstance(message["error"], dict) else {}
                future.set_exception(McpError(f"{self.config.name}：{err.get('message', '出错了')}"))
            else:
                future.set_result(message.get("result"))
            return
        method = message.get("method")
        if "id" in message:  # 服务端发来的请求
            if method == "ping":
                reply: dict[str, Any] = {"result": {}}
            else:
                reply = {"error": {"code": -32601, "message": f"艾拉不支持 {method}"}}
            with contextlib.suppress(McpError):
                await self._send({"jsonrpc": "2.0", "id": message["id"], **reply})
        elif method == "notifications/tools/list_changed" and self.on_tools_changed:
            self.on_tools_changed()

    async def _drain_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        while True:
            line = await self.proc.stderr.readline()
            if not line:
                return
            text = line.decode("utf-8", "replace").rstrip()
            if text:
                self.log.append(text[:300])

    def _fail_pending(self, reason: str) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(McpError(reason))

    async def list_tools(self) -> list[McpTool]:
        tools: list[McpTool] = []
        cursor: str | None = None
        for _ in range(20):  # 分页，最多 20 页
            result = await self.request("tools/list", {"cursor": cursor} if cursor else {}, START_TIMEOUT)
            for raw in (result or {}).get("tools", []) if isinstance(result, dict) else []:
                if not isinstance(raw, dict) or not isinstance(raw.get("name"), str):
                    continue
                schema = raw.get("inputSchema") if isinstance(raw.get("inputSchema"), dict) else {}
                schema = {"type": "object", "properties": {}, **schema}
                annotations = raw.get("annotations") if isinstance(raw.get("annotations"), dict) else {}
                tools.append(McpTool(
                    server=self.config.name,
                    name=raw["name"],
                    description=str(raw.get("description") or raw.get("title") or raw["name"])[:DESCRIPTION_CHARS],
                    schema=schema,
                    read_only=annotations.get("readOnlyHint") is True,
                ))
            cursor = result.get("nextCursor") if isinstance(result, dict) else None
            if not cursor:
                break
        return tools

    async def call(self, tool: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        result = await self.request("tools/call", {"name": tool, "arguments": arguments}, self.config.timeout)
        return render_result(result)

    async def close(self) -> None:
        self._closed = True
        proc = self.proc
        if proc and proc.returncode is None:
            with contextlib.suppress(Exception):
                if proc.stdin:
                    proc.stdin.close()
            try:
                await asyncio.wait_for(proc.wait(), 2)
            except asyncio.TimeoutError:
                await asyncio.to_thread(kill_tree, proc.pid)
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(proc.wait(), 5)
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(BaseException):
                await task
        self._fail_pending(f"{self.config.name} 已经停了。")


# ---------- 所有服务 ----------


@dataclass
class ServerState:
    config: ServerConfig
    status: str = "off"  # off / starting / ready / failed / disabled / unsupported
    error: str = ""
    tools: list[McpTool] = field(default_factory=list)
    client: StdioClient | None = None
    task: asyncio.Task[None] | None = None


class McpManager:
    def __init__(self, path: Path, client_factory: Callable[..., StdioClient] = StdioClient,
                 extensions: Any = None) -> None:
        self.path = path
        self.client_factory = client_factory
        # 扩展包带的服务（app/extensions.py 的 ExtensionStore）：和 mcp.json 里的合在一起用，
        # 但开关、「以后都不用问」记在扩展自己的状态里，增删只能在「扩展」里做
        self.extensions = extensions
        # 配置改了以后叫一声（对话那边用它重连，不用重启 AI 服务）
        self.on_change: Callable[[], Awaitable[None]] | None = None
        self.servers: dict[str, ServerState] = {}
        self._started = False
        self._lock = asyncio.Lock()

    # ----- 配置文件 -----

    def _read_file(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _key(self, data: dict[str, Any]) -> str:
        """文件里用的是 servers（对话那边的例子）还是 mcpServers（Claude Desktop）：写回时照原样"""
        return "servers" if isinstance(data.get("servers"), dict) and not isinstance(data.get("mcpServers"), dict) else "mcpServers"

    def _read_raw(self) -> dict[str, Any]:
        data = self._read_file()
        servers = data.get(self._key(data))
        return servers if isinstance(servers, dict) else {}

    def configs(self) -> dict[str, ServerConfig]:
        out: dict[str, ServerConfig] = {}
        for name, raw in self._read_raw().items():
            try:
                out[name] = ServerConfig.parse(name, raw)
            except McpError as err:
                log.warning("MCP 配置 %s 不对：%s", name, err)
        for name, raw in (self.extensions.mcp_servers() if self.extensions else {}).items():
            if name in out:  # mcp.json 里你自己配的同名服务优先
                continue
            try:
                out[name] = ServerConfig.parse(name, raw)
            except McpError as err:
                log.warning("扩展的 MCP 服务 %s 不对：%s", name, err)
        return out

    def _owner(self, name: str) -> tuple[str, str] | None:
        """扩展带的服务：（扩展 id，服务名）；mcp.json 里的返回 None"""
        if self.extensions is None or name in self._read_raw():
            return None
        return self.extensions.owner(name)

    def _write(self, configs: dict[str, ServerConfig]) -> None:
        data = self._read_file()
        key = self._key(data)
        # 扩展带的服务不写进 mcp.json
        data[key] = {n: c.to_raw() for n, c in configs.items() if not c.extra.get("extension")}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # ----- 启动、停止 -----

    async def ensure_started(self) -> None:
        """第一次用到时按配置把服务都开起来（在后台，不等它们好）"""
        if self._started:
            return
        self._started = True
        for name, config in self.configs().items():
            self._launch(name, config)

    def _launch(self, name: str, config: ServerConfig) -> None:
        state = ServerState(config)
        self.servers[name] = state
        if config.disabled:
            state.status = "disabled"
        elif not config.command:
            state.status, state.error = "unsupported", "编程任务暂时只支持在本机启动的服务（command），远程（url）服务只有对话里能用。"
        else:
            state.status = "starting"
            state.task = asyncio.create_task(self._start(state))

    async def _start(self, state: ServerState) -> None:
        client = self.client_factory(state.config, lambda: self._tools_changed(state))
        state.client = client
        try:
            await client.start()
            state.tools = await client.list_tools()
            if state.config.read_only:  # 你给整个服务标了只读
                for tool in state.tools:
                    tool.read_only = True
            state.status, state.error = "ready", ""
        except (McpError, OSError) as err:
            state.status, state.error, state.tools = "failed", str(err), []
            await client.close()
        except asyncio.CancelledError:
            await client.close()
            raise

    def _tools_changed(self, state: ServerState) -> None:
        async def refresh() -> None:
            if state.client and state.client.alive:
                with contextlib.suppress(McpError):
                    state.tools = await state.client.list_tools()
        asyncio.get_running_loop().create_task(refresh())

    async def wait_ready(self, timeout: float = 15.0) -> None:
        """等还在启动的服务（最多 timeout 秒）：一轮开始前调用，免得刚开机时她看不到工具"""
        await self.ensure_started()
        tasks = [s.task for s in self.servers.values() if s.task and not s.task.done()]
        if tasks:
            await asyncio.wait(tasks, timeout=timeout)

    async def restart(self, name: str) -> None:
        async with self._lock:
            await self.ensure_started()
            await self._stop(name)
            config = self.configs().get(name)
            if config is None:
                self.servers.pop(name, None)
                raise McpError(f"没有叫 {name} 的服务。")
            self._launch(name, config)
        task = self.servers[name].task
        if task:
            await asyncio.wait([task], timeout=START_TIMEOUT)

    async def _stop(self, name: str) -> None:
        state = self.servers.pop(name, None)
        if state is None:
            return
        if state.task and not state.task.done():
            state.task.cancel()
            with contextlib.suppress(BaseException):
                await state.task
        if state.client:
            await state.client.close()

    async def stop_all(self) -> None:
        for name in list(self.servers):
            await self._stop(name)

    async def stop_extension(self, ext_id: str) -> None:
        """升级、卸载扩展前先停掉它的服务（Windows 上进程开着的文件删不掉）"""
        async with self._lock:
            for name, state in list(self.servers.items()):
                if state.config.extra.get("extension") == ext_id:
                    await self._stop(name)

    async def reload(self) -> None:
        """扩展装了、卸了、开关、改了设置以后：按现在的配置补开、停掉、重启有变化的服务"""
        async with self._lock:
            if self._started:
                configs = self.configs()
                for name in list(self.servers):
                    if name not in configs:
                        await self._stop(name)
                for name, config in configs.items():
                    state = self.servers.get(name)
                    if state is None:
                        self._launch(name, config)
                    elif state.config.to_raw() != config.to_raw():
                        await self._stop(name)
                        self._launch(name, config)
        await self._changed()

    # ----- 改配置（设置页） -----

    async def save(self, config: ServerConfig, keep_env: bool = False) -> None:
        """加一个或改一个服务，然后重启它。keep_env：界面没给 env 的值时保留原来的（密钥不回传给界面）"""
        if self._owner(config.name):
            raise McpError(f"{config.name} 是扩展带的服务，到设置里的「扩展」去管。")
        configs = self.configs()
        old = configs.get(config.name)
        if old and keep_env:
            config.env = {**old.env, **config.env}
        if old and not config.always_allow:
            config.always_allow = old.always_allow
        configs[config.name] = config
        self._write(configs)
        await self.restart(config.name)
        await self._changed()

    async def remove(self, name: str) -> None:
        if self._owner(name):
            raise McpError(f"{name} 是扩展带的服务，要删就到设置里的「扩展」卸载这个扩展。")
        configs = self.configs()
        if name not in configs:
            raise McpError(f"没有叫 {name} 的服务。")
        del configs[name]
        self._write(configs)
        async with self._lock:
            await self._stop(name)
        await self._changed()

    async def set_disabled(self, name: str, disabled: bool) -> None:
        configs = self.configs()
        if name not in configs:
            raise McpError(f"没有叫 {name} 的服务。")
        if owner := self._owner(name):
            from ..extensions import ExtensionError

            try:
                self.extensions.set_server_disabled(*owner, disabled)
            except ExtensionError as err:
                raise McpError(str(err)) from err
        else:
            configs[name].disabled = disabled
            self._write(configs)
        await self.restart(name)
        await self._changed()

    async def _changed(self) -> None:
        if self.on_change is None:
            return
        try:
            await self.on_change()
        except Exception:  # 对话那边重连失败不影响这边已经保存的配置
            log.exception("通知 MCP 配置改了的时候出错")

    # ----- 权限：「这个工具以后都不用问」 -----

    def _split(self, full_name: str) -> tuple[str, str] | None:
        for state in self.servers.values():
            for tool in state.tools:
                if tool.full_name == full_name:
                    return state.config.name, tool.name
        return None

    def allowed(self, full_name: str) -> bool:
        found = self._split(full_name)
        if not found:
            return False
        config = self.configs().get(found[0])
        return bool(config and found[1] in config.always_allow)

    def allow(self, full_name: str) -> None:
        found = self._split(full_name)
        if not found:
            return
        if owner := self._owner(found[0]):
            self.extensions.allow(*owner, found[1])
            return
        configs = self.configs()
        config = configs.get(found[0])
        if config and found[1] not in config.always_allow:
            config.always_allow.append(found[1])
            self._write(configs)

    def forget(self, server: str, tool: str) -> bool:
        if owner := self._owner(server):
            return self.extensions.forget(*owner, tool)
        configs = self.configs()
        config = configs.get(server)
        if not config or tool not in config.always_allow:
            return False
        config.always_allow.remove(tool)
        self._write(configs)
        return True

    # ----- 给她用 -----

    def tools(self) -> list[McpTool]:
        """准备好的服务的工具：按服务名、再按服务给的顺序（顺序固定，前缀缓存才稳）"""
        out: list[McpTool] = []
        seen: set[str] = set()
        for name in sorted(self.servers):
            state = self.servers[name]
            if state.status != "ready":
                continue
            for tool in state.tools:
                if tool.full_name not in seen:
                    seen.add(tool.full_name)
                    out.append(tool)
        return out

    async def call(self, server: str, tool: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        state = self.servers.get(server)
        if state is None or state.status != "ready" or not state.client:
            raise McpError(f"MCP 服务 {server} 现在没在运行，去设置页看看。")
        if not state.client.alive:
            state.status, state.error = "failed", "服务进程退出了。"
            raise McpError(f"MCP 服务 {server} 已经退出了，去设置页重启。")
        return await state.client.call(tool, arguments)

    def status(self) -> list[dict[str, Any]]:
        out = []
        configs = self.configs()
        for name in sorted(set(configs) | set(self.servers)):
            state = self.servers.get(name)
            config = configs.get(name) or (state.config if state else None)
            if config is None:
                continue
            out.append({
                "name": name,
                "command": config.command_line(),
                "label": str(config.extra.get("name") or name),
                "extension": config.extra.get("extension") or None,  # 扩展带的服务：扩展 id
                "readOnly": config.read_only,
                "envKeys": sorted(config.env),  # 只给名字，不给值
                "disabled": config.disabled,
                "status": state.status if state else ("disabled" if config.disabled else "off"),
                "error": state.error if state else "",
                "server": (state.client.server_info if state and state.client else {}) or {},
                "tools": [{"name": t.name, "fullName": t.full_name, "description": t.description[:200], "readOnly": t.read_only,
                           "alwaysAllow": t.name in config.always_allow} for t in (state.tools if state else [])],
                "log": list(state.client.log)[-LOG_LINES:] if state and state.client else [],
            })
        return out


def bridge_tools(manager: McpManager) -> list:  # -> list[CodingTool]
    """MCP 工具 → 编程 Agent 的工具（kind：只读的是 mcp_read，其他是 mcp）"""
    from .tools import CodingTool, Outcome

    def make(tool: McpTool) -> CodingTool:
        async def run(args: dict[str, Any]) -> Outcome:
            try:
                text, is_error = await manager.call(tool.server, tool.name, args)
            except McpError as err:
                return Outcome(str(err), ok=False)
            return Outcome(text, ok=not is_error, extra={"mcp": {"server": tool.server, "tool": tool.name}})

        def preview(args: dict[str, Any]) -> str:
            body = json.dumps(args, ensure_ascii=False, indent=2)
            if len(body) > 3000:
                body = body[:3000] + "\n……"
            return f"MCP 服务 {tool.server} 的工具 {tool.name}\n参数：\n{body}"

        return CodingTool(
            tool.full_name,
            f"[MCP · {tool.server}] {tool.description}",
            tool.schema,
            run,
            (lambda _: "mcp_read") if tool.read_only else (lambda _: "mcp"),
            lambda _: f"用 {tool.server} 的 {tool.name}",
            preview,
        )

    return [make(t) for t in manager.tools()]


def is_mcp_tool(name: str) -> bool:
    return name.startswith(TOOL_PREFIX)

