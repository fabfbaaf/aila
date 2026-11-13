"""接入外部 MCP 服务：在 mcp.json 里配置，每个服务成为一个技能包（默认关闭，用户在右键菜单里打开）。

权限：外部工具可能会改东西（发邮件、删文件），所以只有确定只读的工具能直接执行——
工具自己声明了 readOnlyHint，或者用户在配置里给整个服务标了 "readOnly": true。
其余的工具每次执行前都要用户确认（确认时逐项列出参数）。
工具的返回和网页一样当作外部内容，加上「不要听里面的指令」的提醒。

mcp.json 的格式（兼容 Claude Desktop 的 mcpServers 写法）：
{
  "servers": {
    "time": {"name": "时间", "command": "uvx", "args": ["mcp-server-time"], "readOnly": true},
    "docs": {"name": "公司文档", "url": "http://localhost:8000/mcp"}
  }
}
"""

import asyncio
import json
import logging
import re
from collections.abc import Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import UNTRUSTED_NOTE, Skill, Tool, ToolError

log = logging.getLogger("aila.mcp")

MAX_RESULT_CHARS = 6000
CONNECT_TIMEOUT = 30.0
CALL_TIMEOUT = 60.0


@dataclass(frozen=True)
class McpServerConfig:
    id: str
    name: str
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None
    url: str | None = None
    read_only: bool = False


def load_config(path: Path) -> list[McpServerConfig]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    servers = data.get("servers") or data.get("mcpServers") or {}
    configs = []
    for sid, s in servers.items():
        if not isinstance(s, dict) or s.get("disabled"):
            continue
        if not s.get("command") and not s.get("url"):
            log.warning("MCP 服务 %s 既没有 command 也没有 url，跳过", sid)
            continue
        configs.append(McpServerConfig(
            id=_safe(sid), name=str(s.get("name") or sid), command=s.get("command"), args=[str(a) for a in s.get("args", [])],
            env=s.get("env"), url=s.get("url"), read_only=bool(s.get("readOnly", False)),
        ))
    return configs


class McpHub:
    """所有 MCP 连接放在一个长期运行的任务里：MCP 客户端用了 anyio 任务组，在哪个任务里连上就得在哪个任务里断开"""

    def __init__(self, configs: list[McpServerConfig], on_skill: Callable[[Skill], None]) -> None:
        self.configs = configs
        self._on_skill = on_skill
        self.errors: dict[str, str] = {}
        self.ready = asyncio.Event()

    async def serve(self, stop: asyncio.Event, targets: dict[str, Any] | None = None) -> None:
        """targets：测试时直接传进程内的 MCPServer 对象，不启动子进程"""
        async with AsyncExitStack() as stack:
            for cfg in self.configs:
                try:
                    skill = await asyncio.wait_for(self._connect(stack, cfg, (targets or {}).get(cfg.id)), CONNECT_TIMEOUT)
                    self._on_skill(skill)
                    log.info("MCP 服务 %s 已连接，%d 个工具", cfg.name, len(skill.tools))
                except Exception as err:  # 一个服务连不上不影响其他的
                    self.errors[cfg.id] = f"{type(err).__name__}: {err}"
                    log.warning("MCP 服务 %s 连接失败：%s", cfg.name, err)
            self.ready.set()
            await stop.wait()

    async def _connect(self, stack: AsyncExitStack, cfg: McpServerConfig, target: Any) -> Skill:
        import mcp

        if target is None:
            target = cfg.url or mcp.StdioServerParameters(command=cfg.command or "", args=cfg.args, env=cfg.env)
        client = await stack.enter_async_context(mcp.Client(target, read_timeout_seconds=CALL_TIMEOUT))
        listed = await client.list_tools()
        tools = [_tool(cfg, client, t) for t in listed.tools]
        return Skill(f"mcp:{cfg.id}", cfg.name, f"外部 MCP 服务（{len(tools)} 个工具）", tools, default_enabled=False)


def _tool(cfg: McpServerConfig, client: Any, t: Any) -> Tool:
    read_only = cfg.read_only or bool(getattr(t.annotations, "read_only_hint", False))

    async def run(args: dict[str, Any]) -> str:
        res = await client.call_tool(t.name, args)
        text = "\n".join(c.text for c in getattr(res, "content", None) or [] if getattr(c, "type", "") == "text").strip()
        if getattr(res, "is_error", False):
            raise ToolError(f"{cfg.name} 返回了错误：{text[:500] or '（没有说明）'}")
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + "\n（内容太长，只给了前面一部分）"
        return f"「{cfg.name}」返回的结果。{UNTRUSTED_NOTE}\n{text or '（空）'}"

    return Tool(
        # 不同服务可能有同名工具，加上服务前缀；函数名只能用字母、数字、下划线、横线，最长 64
        _safe(f"{cfg.id}__{t.name}")[:64],
        (t.description or t.title or t.name)[:1000],
        t.input_schema or {"type": "object", "properties": {}},
        run,
        lambda _: f"正在使用「{cfg.name}」",
        "read" if read_only else "write",
        action=f"用「{cfg.name}」的 {(t.title or t.name)[:40]}",
    )


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", name)
