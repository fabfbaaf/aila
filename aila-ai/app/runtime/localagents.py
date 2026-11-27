"""本机的编程 Agent（Codex、Claude Code）：找到它们、看登录了没有、准备 ACP 适配器。

艾拉通过 ACP（Agent Client Protocol）和它们说话：每个 Agent 有一个官方的 ACP 适配器（Node 程序，
@agentclientprotocol/codex-acp、@agentclientprotocol/claude-agent-acp）。适配器自己也会带一份 Codex / Claude，
很大（各四五百 MB），所以装的时候不带（--omit=optional），用 CODEX_PATH / CLAUDE_CODE_EXECUTABLE 指到你已经装好的那个。

- 找程序：PATH 里的 codex / claude（npm 装的 codex 在 PATH 里是个脚本，顺着找到里面真正的 codex.exe），
  还有桌面版 Codex 自己带的那份；有几个就用版本最新的（新模型要新的 Codex 才认）
- 登录：用它们自己的命令问（codex login status、claude auth status），只看登没登录，不碰令牌文件
- 登录本身要你在终端里用官方程序做（codex login、claude auth login），艾拉不经手账号
- 适配器装在 数据目录/acp（设置页点「装适配器」才下载，要 Node.js）
"""

from __future__ import annotations

import asyncio
import json
import os
import re
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
    # 自己就会说 ACP（不用适配器）：直接起它，带上这些参数
    native: bool = False
    acp_args: tuple[str, ...] = ()
    # 「改东西前都先请求批准」是哪个模式：艾拉的权限规则靠它（每个批准请求交给 permissions.Policy）
    ask_mode: str = "default"
    # 只读沙箱里要提醒它「要改就请求批准」，不然它直接放弃（Codex）
    ask_note: bool = False
    # 当对话的大脑时，艾拉的人格怎么交给它：override 整个换掉 / append 接在它原来的后面
    persona_mode: str = "append"
    # 除了 PATH，还在这些地方找（相对 %LOCALAPPDATA%，可以带通配符）：桌面版自带的、官方安装器装的
    app_paths: tuple[str, ...] = ()


ENGINES: dict[str, Engine] = {
    "codex": Engine(
        "codex", "Codex", "codex", "@agentclientprotocol/codex-acp", "2.12.1",
        "@agentclientprotocol/codex-acp/dist/index.js", "CODEX_PATH",
        ("login", "status"), "在终端里运行 codex login，用 ChatGPT 账号登录",
        "先装 Codex：npm install -g @openai/codex", ("OPENAI_API_KEY", "CODEX_API_KEY"),
        ask_mode="read-only", ask_note=True, app_paths=("OpenAI/Codex/bin/*/codex.exe",),
    ),
    "claude": Engine(
        "claude", "Claude Code", "claude", "@agentclientprotocol/claude-agent-acp", "0.113.0",
        "@agentclientprotocol/claude-agent-acp/dist/index.js", "CLAUDE_CODE_EXECUTABLE",
        ("auth", "status"), "在终端里运行 claude auth login，用官方程序登录",
        "先装 Claude Code：https://claude.com/claude-code", ("ANTHROPIC_API_KEY",),
        persona_mode="override",
    ),
    # Google 的 Antigravity（反重力）命令行：自己就支持 ACP（agy acp）；--process-per-session 才收 HTTP 的 MCP（派工、对话工具要用）
    "antigravity": Engine(
        "antigravity", "Antigravity", "agy", "", "", "", "",
        (), "在终端里运行 agy，用 Google 账号登录一次",
        "先装 Antigravity 命令行：在 PowerShell 里运行 irm https://antigravity.google/cli/install.ps1 | iex",
        native=True, acp_args=("acp", "--process-per-session"), app_paths=("agy/bin/agy.exe",),
    ),
    # 腾讯的 CodeBuddy Code（WorkBuddy 背后的那个）：命令行自己就支持 ACP（codebuddy --acp），也收 HTTP 的 MCP。
    # 登录和 WorkBuddy 桌面版不通用，要在它的命令行里登录一次；WorkBuddy 桌面版带的精简版单独跑不起来，用正式的命令行
    "codebuddy": Engine(
        "codebuddy", "CodeBuddy", "codebuddy", "", "", "", "",
        (), "在终端里运行 codebuddy.cmd，按提示登录一次（国内账号选微信登录）",
        "先装 CodeBuddy 命令行：npm.cmd install -g @tencent-ai/codebuddy-code",
        native=True, acp_args=("--acp",), app_paths=("codebuddy/bin/codebuddy.exe",),
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
    native: bool = False  # 自己就会说 ACP，不用适配器
    ready: bool = False
    problem: str = ""  # 不能用的原因，写给用户看
    hints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def adapters_dir(data_dir: Path) -> Path:
    return Path(os.environ.get("AILA_ACP_DIR") or data_dir / "acp")


def _on_path(engine: Engine, which=shutil.which) -> str | None:
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


def _app_copies(engine: Engine, local_app_data: str | None = None) -> list[str]:
    """桌面版 Codex 自己带的那份（%LOCALAPPDATA%\\OpenAI\\Codex\\bin\\<版本号>\\codex.exe）：
    它跟着桌面版更新，常常比 npm 装的新——新模型（比如 GPT-6.2）要新的 Codex 才认"""
    base = local_app_data if local_app_data is not None else os.environ.get("LOCALAPPDATA", "")
    if not base or not engine.app_paths:
        return []
    return [str(p) for pattern in engine.app_paths for p in sorted(Path(base).glob(pattern))]


def _version_key(text: str) -> tuple[int, ...]:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)


def find_program(engine: Engine, which=shutil.which, run=None, local_app_data: str | None = None) -> str | None:
    """用哪个程序：PATH 里的和桌面版带的都看，挑版本最新的（问不出版本的排最后）"""
    candidates = [p for p in [_on_path(engine, which), *_app_copies(engine, local_app_data)] if p]
    if len(candidates) <= 1:
        return candidates[0] if candidates else None
    run = run or _run
    return max(candidates, key=lambda p: _version_key(run([p, "--version"])[1]))


def _run(args: list[str], timeout: float = CHECK_TIMEOUT) -> tuple[int, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return -1, ""
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def logged_in(engine: Engine, program: str, run=_run) -> bool | None:
    """问它自己登没登录：codex login status（登录了退出码 0）；claude auth status（JSON 里的 loggedIn）。
    没有这种命令的（Antigravity）问不出来，开会话时没登录它会说"""
    if not engine.login_cmd:
        return None
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


def detect(data_dir: Path, which=shutil.which, run=_run, local_app_data: str | None = None) -> list[LocalAgent]:
    root = adapters_dir(data_dir) / "node_modules"
    node = which("node")
    out = []
    for engine in ENGINES.values():
        info = LocalAgent(engine.id, engine.title)
        info.path = find_program(engine, which, run, local_app_data)
        info.native = engine.native
        info.adapter = engine.native or (root / engine.script).is_file()
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
        if engine.native:
            pass  # 自己就会说 ACP：不用 Node、不用适配器
        elif not node:
            info.problem = info.problem or "没找到 Node.js（适配器要用）"
            info.hints.append("装一个 Node.js：https://nodejs.org")
        elif not info.adapter:
            info.problem = info.problem or "还没装适配器"
            info.hints.append("在 设置 → 编程 → 本机 Agent 里点「装适配器」")
        info.ready = bool(info.path and (engine.native or (node and info.adapter)) and info.logged_in is not False)
        out.append(info)
    return out


def launch(engine_id: str, data_dir: Path, which=shutil.which, local_app_data: str | None = None) -> tuple[str, list[str], dict[str, str]]:
    """起适配器的命令和环境变量：node 适配器入口，指到你装好的程序，去掉会改成按量计费的密钥"""
    engine = ENGINES.get(engine_id)
    if engine is None:
        raise LocalAgentError(f"不认识的 Agent：{engine_id}")
    node = which("node")
    program = find_program(engine, which, local_app_data=local_app_data)
    if not program:
        raise LocalAgentError(f"没找到 {engine.title}。{engine.install_hint}")
    if engine.native:
        return program, list(engine.acp_args), {k: v for k, v in os.environ.items() if k not in engine.drop_env}
    script = adapters_dir(data_dir) / "node_modules" / engine.script
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
    packages = [f"{e.package}@{e.version}" for e in ENGINES.values() if not e.native]
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
