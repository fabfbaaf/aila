"""本机的编程 Agent（Codex、Claude Code）：找到它们、看登录了没有、准备 ACP 适配器。

艾拉通过 ACP（Agent Client Protocol）和它们说话：每个 Agent 有一个官方的 ACP 适配器（Node 程序，
@agentclientprotocol/codex-acp、@agentclientprotocol/claude-agent-acp）。适配器自己也会带一份 Codex / Claude，
很大（各四五百 MB），所以装的时候不带（--omit=optional），用 CODEX_PATH / CLAUDE_CODE_EXECUTABLE 指到你已经装好的那个。

- 找程序：先看 PATH 里的 codex / claude；npm 装的 codex 在 PATH 里是个脚本，顺着找到里面真正的 codex.exe
- 登录：用它们自己的命令问（codex login status、claude auth status），只看登没登录，不碰令牌文件
- 登录本身要你在终端里用官方程序做（codex login、claude auth login），艾拉不经手账号
- 适配器装在 数据目录/acp（设置页点「装适配器」才下载，要 Node.js）
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

ADAPTER_TIMEOUT = 300  # 秒：装适配器最多等这么久
CHECK_TIMEOUT = 20  # 秒：问版本、问登录


@dataclass(frozen=True)
class Engine:
    id: str
    title: str
    command: str  # PATH 里的名字
    package: str  # 适配器的 npm 包
    version: str  # 测过的适配器版本
    script: str  # 适配器入口（相对 node_modules）
    exe_env: str  # 告诉适配器用哪个程序的环境变量
    login_cmd: tuple[str, ...]
    login_hint: str
    install_hint: str
    # 去掉的环境变量：有它们时 Agent 会改用 API 按量计费，而不是你的订阅登录
    drop_env: tuple[str, ...] = ()


ENGINES: dict[str, Engine] = {
    "codex": Engine(
        "codex", "Codex", "codex", "@agentclientprotocol/codex-acp", "2.12.1",
        "@agentclientprotocol/codex-acp/dist/index.js", "CODEX_PATH",
        ("login", "status"), "在终端里运行 codex login，用 ChatGPT 账号登录",
        "先装 Codex：npm install -g @openai/codex", ("OPENAI_API_KEY", "CODEX_API_KEY"),
    ),
    "claude": Engine(
        "claude", "Claude Code", "claude", "@agentclientprotocol/claude-agent-acp", "0.113.0",
        "@agentclientprotocol/claude-agent-acp/dist/index.js", "CLAUDE_CODE_EXECUTABLE",
        ("auth", "status"), "在终端里运行 claude auth login，用官方程序登录",
        "先装 Claude Code：https://claude.com/claude-code", ("ANTHROPIC_API_KEY",),
    ),
}


@dataclass
class LocalAgent:
    """一个本机 Agent 现在的情况（设置页、新任务的选项用）"""

    id: str
    title: str
    path: str | None = None
    version: str = ""
    logged_in: bool | None = None  # None：问不出来
    adapter: bool = False
    ready: bool = False
    problem: str = ""  # 不能用的原因，写给用户看
    hints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def adapters_dir(data_dir: Path) -> Path:
    return Path(os.environ.get("AILA_ACP_DIR") or data_dir / "acp")


def find_program(engine: Engine, which=shutil.which) -> str | None:
    """PATH 里找；npm 装的 codex 是个脚本（codex.cmd），顺着找到里面的 codex.exe（适配器要真正的程序）"""
    found = which(engine.command)
    if not found:
        return None
    path = Path(found)
    if engine.id == "codex" and path.suffix.lower() in ("", ".cmd", ".ps1"):
        package = path.parent / "node_modules" / "@openai" / "codex"
        for exe in sorted(package.glob("**/vendor/*/bin/codex.exe")) + sorted(package.glob("**/vendor/*/codex/codex.exe")):
            return str(exe)
    return str(path)


def _run(args: list[str], timeout: float = CHECK_TIMEOUT) -> tuple[int, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return -1, ""
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def logged_in(engine: Engine, program: str, run=_run) -> bool | None:
    """问它自己登没登录：codex login status（登录了退出码 0）；claude auth status（JSON 里的 loggedIn）"""
    code, out = run([program, *engine.login_cmd])
    if engine.id == "claude":
        try:
            start = out.index("{")
            return bool(json.loads(out[start : out.rindex("}") + 1]).get("loggedIn"))
        except ValueError:
            return None
    if code == -1:
        return None
    return code == 0 and "not logged in" not in out.lower()


def detect(data_dir: Path, which=shutil.which, run=_run) -> list[LocalAgent]:
    root = adapters_dir(data_dir) / "node_modules"
    node = which("node")
    out = []
    for engine in ENGINES.values():
        info = LocalAgent(engine.id, engine.title)
        info.path = find_program(engine, which)
        info.adapter = (root / engine.script).is_file()
        if not info.path:
            info.problem = f"没找到 {engine.title}"
            info.hints.append(engine.install_hint)
            out.append(info)
            continue
        _code, version = run([info.path, "--version"])
        info.version = version.strip().splitlines()[0][:80] if version.strip() else ""
        info.logged_in = logged_in(engine, info.path, run)
        if info.logged_in is False:
            info.problem = f"{engine.title} 还没登录"
            info.hints.append(engine.login_hint)
        if not node:
            info.problem = info.problem or "没找到 Node.js（适配器要用）"
            info.hints.append("装一个 Node.js：https://nodejs.org")
        elif not info.adapter:
            info.problem = info.problem or "还没装适配器"
            info.hints.append("在 设置 → 编程 → 本机 Agent 里点「装适配器」")
        info.ready = bool(info.path and node and info.adapter and info.logged_in is not False)
        out.append(info)
    return out


def launch(engine_id: str, data_dir: Path, which=shutil.which) -> tuple[str, list[str], dict[str, str]]:
    """起适配器的命令和环境变量：node 适配器入口，指到你装好的程序，去掉会改成按量计费的密钥"""
    engine = ENGINES.get(engine_id)
    if engine is None:
        raise LocalAgentError(f"不认识的 Agent：{engine_id}")
    node = which("node")
    program = find_program(engine, which)
    script = adapters_dir(data_dir) / "node_modules" / engine.script
    if not program:
        raise LocalAgentError(f"没找到 {engine.title}。{engine.install_hint}")
    if not node:
        raise LocalAgentError("没找到 Node.js，适配器跑不起来。装一个 Node.js 再试。")
    if not script.is_file():
        raise LocalAgentError(f"{engine.title} 的适配器还没装：在 设置 → 编程 → 本机 Agent 里点「装适配器」。")
    env = {k: v for k, v in os.environ.items() if k not in engine.drop_env}
    env[engine.exe_env] = program
    return node, [str(script)], env


async def install_adapters(data_dir: Path, which=shutil.which) -> str:
    """装两个适配器（不带它们自带的 Codex / Claude）。返回给用户看的一句话；失败抛 LocalAgentError"""
    npm = which("npm")
    if not npm:
        raise LocalAgentError("没找到 npm（装 Node.js 时会带上）。装好 Node.js 再试。")
    root = adapters_dir(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / "package.json"
    if not manifest.exists():
        manifest.write_text('{"private": true}\n', encoding="utf-8")
    packages = [f"{e.package}@{e.version}" for e in ENGINES.values()]
    proc = await asyncio.create_subprocess_exec(
        npm, "install", "--omit=optional", "--no-audit", "--no-fund", *packages,
        cwd=str(root), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), ADAPTER_TIMEOUT)
    except TimeoutError:
        proc.kill()
        raise LocalAgentError("装适配器超时了，看看网络再试。") from None
    if proc.returncode != 0:
        tail = out.decode("utf-8", "replace").strip().splitlines()[-3:]
        raise LocalAgentError("装适配器没成功：" + " / ".join(tail)[:300])
    return "适配器装好了。"


class LocalAgentError(Exception):
    """给用户看的原因"""


__all__ = ["ENGINES", "Engine", "LocalAgent", "LocalAgentError", "adapters_dir", "detect", "find_program",
           "install_adapters", "launch", "logged_in"]
