"""检测本机编程 Agent，并准备启动各自的 ACP 进程。

艾拉通过 ACP（Agent Client Protocol）和它们说话。Codex、Claude Code 有单独的适配器（Node 程序，
@agentclientprotocol/codex-acp、@agentclientprotocol/claude-agent-acp）。适配器自己也会带一份 Codex / Claude，
很大（各四五百 MB），所以装的时候不带（--omit=optional），用 CODEX_PATH / CLAUDE_CODE_EXECUTABLE 指到你已经装好的那个。
其他 Agent 直接启动官方命令行的 ACP 模式。

- 找程序：PATH 里的 codex / claude（npm 装的 codex 在 PATH 里是个脚本，顺着找到里面真正的 codex.exe），
  还有桌面版 Codex 自己带的那份；有几个就用版本最新的（新模型要新的 Codex 才认）
- 登录：用它们自己的命令问（codex login status、claude auth status），只看登没登录，不碰令牌文件
- 登录在厂商的 CLI 里完成，艾拉只打开对应的命令窗口
- 适配器优先用本机已有的文件。也可以在设置页安装，或在首次使用时下载到数据目录
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Literal

ADAPTER_TIMEOUT = 300  # 秒：装适配器最多等这么久
CHECK_TIMEOUT = 20  # 秒：问版本、问登录
AgentEngine = Literal["codex", "claude", "antigravity", "codebuddy", "copilot", "kimi", "opencode", "cursor", "qwen", "minimax", "codex-alt", "claude-alt"]
CHAT_ENGINES = frozenset({"codex", "claude", "antigravity", "codebuddy"})


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
    # 「改东西前都先请求批准」的 ACP 模式。有些 CLI 在启动时配置审批，不提供这个模式选项。
    ask_mode: str = "default"
    ask_option: str = "mode"
    # 只对启动的子进程生效，不修改用户的 CLI 配置。
    launch_env: tuple[tuple[str, str], ...] = ()
    # 只读沙箱里要提醒它「要改就请求批准」，不然它直接放弃（Codex）
    ask_note: bool = False
    # 当对话的大脑时，艾拉的人格怎么交给它：override 整个换掉 / append 接在它原来的后面
    persona_mode: str = "append"
    # 除了 PATH，还在这些地方找（相对 %LOCALAPPDATA%，可以带通配符）：桌面版自带的、官方安装器装的
    app_paths: tuple[str, ...] = ()
    # 官方安装器装到用户目录的位置。
    home_paths: tuple[str, ...] = ()
    # 当对话的大脑时多带的启动参数：比如关掉它自己的定时任务、命令这些工具，只留调艾拉工具要用的
    brain_args: tuple[str, ...] = ()
    # 外部工具（MCP）要先用它的工具搜索找出来才能调（CodeBuddy）：对话的大脑会把艾拉工具的完整名字告诉它
    deferred_mcp: bool = False
    # 不认 ACP 开会话时给的系统提示词、要从文件读的（CodeBuddy 的 --system-prompt-file，每个新会话重读一次）
    prompt_file_flag: str = ""
    login_run: tuple[str, ...] = ()  # 空参数时打开厂商的交互式终端登录
    account: str = ""  # 模型目录中显示的账号分组名


ENGINES: dict[str, Engine] = {
    "codex": Engine(
        "codex", "Codex", "codex", "@agentclientprotocol/codex-acp", "2.12.1",
        "@agentclientprotocol/codex-acp/dist/index.js", "CODEX_PATH",
        ("login", "status"), "在终端里运行 codex login，用 ChatGPT 账号登录",
        "先装 Codex：npm install -g @openai/codex", ("OPENAI_API_KEY", "CODEX_API_KEY"),
        ask_mode="read-only", ask_note=True, app_paths=("OpenAI/Codex/bin/*/codex.exe",),
        login_run=("login",), account="ChatGPT",
    ),
    "claude": Engine(
        "claude", "Claude Code", "claude", "@agentclientprotocol/claude-agent-acp", "0.113.0",
        "@agentclientprotocol/claude-agent-acp/dist/index.js", "CLAUDE_CODE_EXECUTABLE",
        ("auth", "status"), "在终端里运行 claude auth login，用官方程序登录",
        "先装 Claude Code：https://claude.com/claude-code", ("ANTHROPIC_API_KEY",),
        persona_mode="override", login_run=("auth", "login"), account="Claude",
    ),
    # Google 的 Antigravity（反重力）命令行：自己就支持 ACP（agy acp）；--process-per-session 才收 HTTP 的 MCP（派工、对话工具要用）
    "antigravity": Engine(
        "antigravity", "Antigravity", "agy", "", "", "", "",
        (), "在终端里运行 agy，用 Google 账号登录一次",
        "先装 Antigravity 命令行：在 PowerShell 里运行 irm https://antigravity.google/cli/install.ps1 | iex",
        native=True, acp_args=("acp", "--process-per-session"), app_paths=("agy/bin/agy.exe",),
        account="Google（Antigravity）",
    ),
    # 腾讯的 CodeBuddy Code（WorkBuddy 背后的那个）：命令行自己就支持 ACP（codebuddy --acp），也收 HTTP 的 MCP。
    # 登录和 WorkBuddy 桌面版不通用，要在它的命令行里登录一次；WorkBuddy 桌面版带的精简版单独跑不起来，用正式的命令行
    "codebuddy": Engine(
        "codebuddy", "CodeBuddy", "codebuddy", "", "", "", "",
        (), "在终端里运行 codebuddy.cmd，按提示登录一次（国内账号选微信登录）",
        "先装 CodeBuddy 命令行：npm.cmd install -g @tencent-ai/codebuddy-code",
        native=True, acp_args=("--acp",), app_paths=("codebuddy/bin/codebuddy.exe",),
        # 对话里只留找外部工具、调外部工具这两个（艾拉的工具它要先搜再调），它自己的定时任务、命令都关掉：
        # 不然它会用自己的 CronCreate 记提醒（对话里不批，就什么都没做）
        brain_args=("--tools", "ToolSearch,DeferExecuteTool"), deferred_mcp=True,
        # ACP 给的系统提示词它不认（试过 append、override 都还说自己是混元）：艾拉的人格写进文件交给它
        prompt_file_flag="--system-prompt-file", persona_mode="override",
        account="CodeBuddy",
    ),
    "copilot": Engine(
        "copilot", "GitHub Copilot CLI", "copilot", "", "", "", "",
        (), "在终端里运行 copilot login，用 GitHub 账号登录",
        "安装说明：https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/install-copilot-cli",
        native=True, acp_args=("--acp", "--stdio"), ask_mode="",
        launch_env=(("COPILOT_ASSISTED_APPROVAL", "false"),),
        account="GitHub Copilot",
    ),
    "kimi": Engine(
        "kimi", "Kimi Code", "kimi", "", "", "", "",
        (), "在终端里运行 kimi login，用 Kimi 账号登录",
        "安装说明：https://github.com/MoonshotAI/kimi-code#install",
        native=True, acp_args=("acp",), home_paths=(".kimi-code/bin/kimi.exe", ".kimi-code/bin/kimi"),
        login_run=("login",),
    ),
    "opencode": Engine(
        "opencode", "OpenCode", "opencode", "", "", "", "",
        (), "在终端里运行 opencode auth login，或在 OpenCode 里配置本地模型",
        "安装说明：https://opencode.ai/docs/#install",
        native=True, acp_args=("acp",), ask_mode="build", home_paths=(".opencode/bin/opencode",),
        login_run=("auth", "login"),
    ),
    "cursor": Engine(
        "cursor", "Cursor CLI", "cursor-agent", "", "", "", "",
        ("status",), "在终端里运行 agent login，用 Cursor 账号登录",
        "安装说明：https://prod.cursor.com/docs/cli/installation",
        native=True, acp_args=("acp",), ask_mode="agent",
        app_paths=("cursor-agent/cursor-agent.exe", "cursor-agent/cursor-agent.cmd",
                   "cursor-agent/agent.exe", "cursor-agent/agent.cmd"),
        home_paths=(".local/bin/cursor-agent", ".local/bin/agent"),
        login_run=("login",), account="Cursor",
    ),
    "qwen": Engine(
        "qwen", "Qwen Code", "qwen", "", "", "", "",
        (), "在终端里打开 qwen，输入 /auth 选择登录方式",
        "安装说明：https://qwenlm.github.io/qwen-code-docs/en/users/quickstart/",
        native=True, acp_args=("--acp", "--approval-mode", "default"),
        app_paths=("qwen-code/bin/qwen.cmd",),
    ),
    "minimax": Engine(
        "minimax", "MiniMax Code", "mcode", "", "", "", "",
        (), "在终端里打开 mcode，输入 /login 登录 MiniMax",
        "安装说明：https://github.com/MiniMax-AI/minimax-code",
        native=True, acp_args=("acp",), ask_option="permissionMode",
        home_paths=(".minimax-code/mcode.cmd", ".minimax-code/bin/mcode"),
    ),
}


# 第二账号由厂商 CLI 负责登录，艾拉只把配置和凭据存储的位置交给它。
ACCOUNT_SLOTS: dict[str, tuple[str, str]] = {
    "codex-alt": ("codex", "CODEX_HOME"),
    "claude-alt": ("claude", "CLAUDE_CONFIG_DIR"),
}
for slot, (provider, _) in ACCOUNT_SLOTS.items():
    original = ENGINES[provider]
    ENGINES[slot] = replace(original, id=slot, title=f"{original.title} · 2", account=f"{original.account} · 2")

ACCOUNT_AUTH_OVERRIDES = (
    "CODEX_ACCESS_TOKEN", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY", "ANTHROPIC_BASE_URL",
)


def account_provider(engine_id: str) -> str:
    return ACCOUNT_SLOTS[engine_id][0] if engine_id in ACCOUNT_SLOTS else engine_id


def account_home(engine_id: str, data_dir: Path) -> Path | None:
    return (data_dir / "agent-accounts" / engine_id).resolve() if engine_id in ACCOUNT_SLOTS else None


def account_env(engine_id: str, data_dir: Path) -> dict[str, str]:
    engine = ENGINES[engine_id]
    home = account_home(engine_id, data_dir)
    if home is None:
        return _child_env(engine.drop_env)
    return {**_child_env((*engine.drop_env, *ACCOUNT_AUTH_OVERRIDES)), ACCOUNT_SLOTS[engine_id][1]: str(home)}


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
    account: str = ""
    can_login: bool = False
    installs_adapter: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class LocalAgentError(Exception):
    """给用户看的原因"""


class AdapterMissing(LocalAgentError):
    """适配器还没装：调用的一方可以先装上（install_adapters）再起它"""


def adapters_dir(data_dir: Path) -> Path:
    """自己装的适配器（Claude Code 的第一次用时装在这里）"""
    return Path(os.environ.get("AILA_ACP_DIR") or data_dir / "acp")


def bundled_adapters_dir() -> Path | None:
    """桌面端可以把已有的 ACP 资源目录交给 AI 服务。"""
    value = os.environ.get("AILA_ACP_BUNDLED", "")
    return Path(value) if value else None


def adapter_script(engine: Engine, data_dir: Path) -> Path | None:
    """适配器入口：先看自己装的（可能更新），再看安装包里带的"""
    for root in (adapters_dir(data_dir), bundled_adapters_dir()):
        if root is not None and (root / "node_modules" / engine.script).is_file():
            return root / "node_modules" / engine.script
    return None


def node_runtime(which=shutil.which) -> tuple[str, dict[str, str]] | None:
    """优先用桌面端提供的 Electron Node。单独运行 AI 服务时用 PATH 上的 Node。"""
    bundled = os.environ.get("AILA_NODE", "")
    if bundled and Path(bundled).is_file():
        return bundled, {"ELECTRON_RUN_AS_NODE": "1"}
    node = which("node")
    return (node, {}) if node else None


def npm_command(which=shutil.which) -> tuple[list[str], dict[str, str]] | None:
    """装适配器用的 npm：安装包里带的（AILA_NPM_CLI，用上面的 Node 跑），没有就用电脑上的"""
    cli = os.environ.get("AILA_NPM_CLI", "")
    runtime = node_runtime(which)
    if cli and Path(cli).is_file() and runtime:
        return [runtime[0], cli], runtime[1]
    npm = which("npm")
    return ([npm], {}) if npm else None


def _on_path(engine: Engine, which=shutil.which) -> str | None:
    """PATH 里找；npm 装的 codex 是个脚本（codex.cmd），顺着找到里面的 codex.exe（适配器要真正的程序）"""
    found = which(engine.command)
    if not found:
        return None
    path = Path(found)
    if account_provider(engine.id) == "codex" and path.suffix.lower() in ("", ".cmd", ".ps1"):
        package = path.parent / "node_modules" / "@openai" / "codex"
        for exe in sorted(package.glob("**/vendor/*/bin/codex.exe")) + sorted(package.glob("**/vendor/*/codex/codex.exe")):
            return str(exe)
    return str(path)


def _app_copies(engine: Engine, local_app_data: str | None = None) -> list[str]:
    """即使安装器刚更新系统 PATH，仍能从它的默认目录找到程序。"""
    base = local_app_data if local_app_data is not None else os.environ.get("LOCALAPPDATA", "")
    paths = [p for pattern in engine.app_paths for p in sorted(Path(base).glob(pattern))] if base else []
    if local_app_data is None and engine.home_paths:
        paths.extend(Path.home() / path for path in engine.home_paths)
    return [str(p) for p in paths if p.is_file()]


def _version_key(text: str) -> tuple[int, ...]:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)


def find_program(engine: Engine, which=shutil.which, run=None, local_app_data: str | None = None) -> str | None:
    """用哪个程序：PATH 里的和桌面版带的都看，挑版本最新的（问不出版本的排最后）"""
    candidates = list(dict.fromkeys(p for p in [_on_path(engine, which), *_app_copies(engine, local_app_data)] if p))
    if len(candidates) <= 1:
        return candidates[0] if candidates else None
    run = run or _run
    return max(candidates, key=lambda p: _version_key(run([p, "--version"])[1]))


def _child_env(exclude: tuple[str, ...] = ()) -> dict[str, str]:
    """保留厂商自己的登录环境，把艾拉的服务配置和密钥留在 AI 服务里。"""
    private = {"LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "SEARCH_API_KEY", "SEARCH_PROVIDER", "ELECTRON_RUN_AS_NODE"}
    private.update(exclude)
    return {k: v for k, v in os.environ.items() if not k.upper().startswith("AILA_") and k.upper() not in private}


def _run(args: list[str], timeout: float = CHECK_TIMEOUT, *, env: dict[str, str] | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           env=_child_env() if env is None else env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return -1, ""
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def logged_in(engine: Engine, program: str, run=_run) -> bool | None:
    """只使用 CLI 公开的登录状态命令。缺少状态命令或结果不明确时，由 Agent 在打开会话时判断。"""
    if not engine.login_cmd:
        return None
    code, out = run([program, *engine.login_cmd])
    if account_provider(engine.id) == "claude":
        try:
            start = out.index("{")
            return bool(json.loads(out[start : out.rindex("}") + 1]).get("loggedIn"))
        except ValueError:
            return None
    if code == -1:
        return None
    if engine.id == "codex-alt":
        return code == 0 and "using chatgpt" in out.lower()
    if engine.id == "cursor":
        status = out.lower()
        if "not authenticated" in status or "not logged in" in status:
            return False
        return True if code == 0 and ("logged in" in status or "authenticated as" in status) else None
    return code == 0 and "not logged in" not in out.lower()


def detect(data_dir: Path, which=shutil.which, run=_run, local_app_data: str | None = None, *, include_profiles: bool = False) -> list[LocalAgent]:
    runtime = node_runtime(which)
    npm = npm_command(which)

    def inspect(engine: Engine) -> LocalAgent:
        info = LocalAgent(engine.id, engine.title, account=engine.account or engine.title)
        info.path = find_program(engine, which, run, local_app_data)
        info.native = engine.native
        info.adapter = engine.native or adapter_script(engine, data_dir) is not None
        if not info.path:
            info.problem = f"没找到 {engine.title}"
            info.hints.append(engine.install_hint)
            return info
        _code, version = run([info.path, "--version"])
        info.can_login = True
        info.version = version.strip().splitlines()[0][:80] if version.strip() else ""
        profile = account_home(engine.id, data_dir)
        if profile is None:
            info.logged_in = logged_in(engine, info.path, run)
        elif profile.is_dir():
            env = account_env(engine.id, data_dir)
            info.logged_in = logged_in(engine, info.path, lambda args: run(args, env=env))
        else:
            info.logged_in = False
        if info.logged_in is False:
            info.problem = f"{engine.title} 还没登录"
            info.hints.append(engine.login_hint)
        elif info.logged_in is None and engine.native:
            info.hints.append(engine.login_hint)
        if not engine.native and not runtime:
            info.problem = info.problem or "没找到 Node.js（适配器要用）"
            info.hints.append("装一个 Node.js：https://nodejs.org")
        elif not info.adapter:
            info.installs_adapter = npm is not None
            if not info.installs_adapter:
                info.problem = info.problem or "适配器还没装，也没有 npm 能装"
                info.hints.append("装一个 Node.js（会带上 npm）：https://nodejs.org")
        login_ready = info.logged_in is True if profile is not None else info.logged_in is not False
        info.ready = bool((engine.native or (runtime and (info.adapter or info.installs_adapter))) and login_ready)
        return info

    # 每个程序单独问版本和登录状态，几个程序慢时也能一起等。列表仍按注册顺序返回。
    with ThreadPoolExecutor(max_workers=len(ENGINES)) as workers:
        entries = (engine for engine in ENGINES.values()
                   if include_profiles or engine.id not in ACCOUNT_SLOTS or account_home(engine.id, data_dir).is_dir())
        return list(workers.map(inspect, entries))


def login_command(engine_id: str, which=shutil.which, local_app_data: str | None = None) -> list[str]:
    """官方的登录命令（桌面端开一个窗口跑它，账号密码在官方程序里填，艾拉不经手）"""
    engine = ENGINES.get(engine_id)
    if engine is None:
        raise LocalAgentError(f"不认识的 Agent：{engine_id}")
    program = find_program(engine, which, local_app_data=local_app_data)
    if not program:
        raise LocalAgentError(f"没找到 {engine.title}。{engine.install_hint}")
    return [program, *engine.login_run]


def launch(engine_id: str, data_dir: Path, which=shutil.which, local_app_data: str | None = None) -> tuple[str, list[str], dict[str, str]]:
    """为已安装的 Agent 生成 ACP 启动命令和子进程环境。"""
    engine = ENGINES.get(engine_id)
    if engine is None:
        raise LocalAgentError(f"不认识的 Agent：{engine_id}")
    program = find_program(engine, which, local_app_data=local_app_data)
    if not program:
        raise LocalAgentError(f"没找到 {engine.title}。{engine.install_hint}")
    profile = account_home(engine_id, data_dir)
    if profile is not None and not profile.is_dir():
        raise LocalAgentError("先在设置里登录第二个账号。")
    env = account_env(engine_id, data_dir)
    env.update(engine.launch_env)
    if engine.id == "opencode":
        _opencode_permissions(program, env)
    if engine.native:
        return program, list(engine.acp_args), env
    runtime = node_runtime(which)
    if not runtime:
        raise LocalAgentError("没找到 Node.js，适配器跑不起来。装一个 Node.js 再试。")
    script = adapter_script(engine, data_dir)
    if script is None:
        raise AdapterMissing(f"{engine.title} 的适配器还没装。")
    node, extra = runtime
    env.update(extra)
    env[engine.exe_env] = program
    return node, [str(script)], env


def _opencode_permissions(program: str, env: dict[str, str]) -> None:
    """两代 OpenCode 的审批配置不同，只在 ACP 子进程里设置。"""
    code, version = _run([program, "--version"])
    major, *_ = _version_key(version)
    if code or major < 1:
        raise LocalAgentError("没读到 OpenCode 的版本，更新 OpenCode 后再试。")
    if major == 1:
        env["OPENCODE_PERMISSION"] = '{"*":"ask","task":"deny"}'
        return
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps({
        "agents": {
            "build": {"permissions": [
                {"action": "*", "resource": "*", "effect": "ask"},
                {"action": "subagent", "resource": "*", "effect": "deny"},
            ]},
        },
    })


_install_lock = asyncio.Lock()


async def install_adapters(data_dir: Path, which=shutil.which, engines: list[str] | None = None) -> str:
    """装适配器（不带它们自带的 Codex / Claude）：engines 不给就装所有还没有的。返回给用户看的一句话；失败抛 LocalAgentError。
    同时只装一次（第一次用时自动装，可能几个地方同时来）"""
    async with _install_lock:
        wanted = list({account_provider(e): ENGINES[account_provider(e)] for e in (engines or ENGINES)
                       if e in ENGINES and not ENGINES[e].native}.values())
        missing = [e for e in wanted if adapter_script(e, data_dir) is None]
        if not missing:
            return "适配器已经装好了。"
        npm = npm_command(which)
        if not npm:
            raise LocalAgentError("没找到 npm（装 Node.js 时会带上）。装好 Node.js 再试。")
        command, extra = npm
        root = adapters_dir(data_dir)
        root.mkdir(parents=True, exist_ok=True)
        manifest = root / "package.json"
        if not manifest.exists():
            manifest.write_text('{"private": true}\n', encoding="utf-8")
        packages = [f"{e.package}@{e.version}" for e in missing]
        proc = await asyncio.create_subprocess_exec(
            *command, "install", "--omit=optional", "--no-audit", "--no-fund", *packages,
            cwd=str(root), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            env={**_child_env(), **extra},
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


__all__ = ["ENGINES", "AgentEngine", "Engine", "LocalAgent", "LocalAgentError", "AdapterMissing", "adapters_dir",
           "adapter_script", "detect", "find_program", "install_adapters", "launch", "logged_in", "login_command",
           "node_runtime", "npm_command"]
