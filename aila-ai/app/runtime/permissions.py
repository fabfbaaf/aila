"""权限：每次工具调用之前决定 允许 / 先问用户 / 拒绝。

四种模式（会话里随时可以换）：
  read_only  只读：不改文件；命令只允许明显只读的那些（git status、ls……）
  ask        先问再改（默认）：读不用问；改文件、跑命令要问，明显只读的命令和你允许过的命令不用问
  auto       工作区内自动：改文件、跑命令都不问，危险命令除外
  full       全部放开：在 auto 之上，打开本机端口、在网页上点和提交、用外部工具（MCP）也不问；
             危险命令和密钥文件照样问（下面「不管哪种模式」那几条不变）

不管哪种模式：
- 危险命令（删除、强推、改系统设置、下载就执行……）始终要问，也不能「以后都允许」
- 读可能存着密钥的文件要问
- 记项目记忆（以后每个会话都会带上）：只读不让记，先问再改要问，自动模式直接记
- MCP 外部工具（mcp.py）：服务标了只读的不用问；其他的只读模式不让用，另外两种模式都要问
  （它们在项目以外做事），可以选「这个工具以后都不用问」
- 联网（web）：搜索、读公网网页，只读，任何模式都不用问（本机和内网地址在读网页时就被拦住）
- 派子 Agent（delegate，workers.py）：只读模式不让派；其他模式不用问——它只改自己的临时分支，
  收回到你的代码时按改文件（edit）问，给你看 diff。子 Agent 自己的权限见 workers.WorkerPolicy
- 浏览器：browser_read 是打开公网网页和查看类操作；browser_get 是普通链接、GET 表单或管理标签页，只读模式不执行。
  browser_local 首次访问本机端口要问，可以按项目记。browser_interact 在本机页面上按权限模式，
  browser_external 在公网页面上做可能产生副作用的操作，任何非只读模式都要问。
- 问的时候可以选「以后都允许」：改文件 → 这个会话里改文件不再问；命令 → 这个项目里这条命令（一字不差）以后不再问，
  记在 data/agent_rules.json

Windows 上没有沙箱：命令真正能做什么只能靠上面这些规则把关。危险命令的识别是尽力而为，不是保证。

电脑任务用独立 ComputerPolicy：只读、先问再改；普通命令记在「电脑」范围。
受保护目录和密钥文件的读取每次问；secret_edit 是受保护编辑，每次问，不能记住。
危险命令和受保护位置不受「以后都允许」影响，也不能记入电脑规则。
"""

import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from .workspace import SAFE_ENV_EXAMPLES, SECRET_DIRS, SECRET_NAME

Mode = Literal["read_only", "ask", "auto", "full"]
MODES: tuple[Mode, ...] = ("read_only", "ask", "auto", "full")
MODE_LABELS = {"read_only": "只读", "ask": "先问再改", "auto": "工作区内自动", "full": "全部放开"}
# 改文件、跑普通命令都不问的模式（电脑任务不能选）
AUTO_MODES: tuple[Mode, ...] = ("auto", "full")


@dataclass(frozen=True)
class Decision:
    verdict: Literal["allow", "ask", "deny"]
    reason: str = ""
    can_remember: bool = False


# 命令里有这些就不算「明显只读」：可能串了别的命令、重定向写文件、或者展开出别的东西
# PowerShell 里括号、@( ) 都会先执行里面的命令（echo (Remove-Item x)），所以括号也算
SHELL_META = re.compile(r"[;&|<>`\n\r()]|\$\(")
# 看起来是查看类的 git 命令，带了这些参数就会写文件或者跑外部程序：--output 写到任意路径，
# --ext-diff / --textconv 跑 git 配置里的外部程序
WRITING_OPTIONS = re.compile(r"(^|\s)--(output|ext-diff|textconv)(=|\s|$)", re.IGNORECASE)

# 明显只读、可以直接跑的命令（整条命令以这些开头，且没有上面的符号）
READ_ONLY_COMMANDS = re.compile(
    r"^(git (status|diff|log|show|blame|rev-parse|ls-files|remote -v|branch( --list| -a| -r| -v| --show-current)?\s*$)"
    r"|ls\b|dir\b|pwd\b|cat\b|head\b|tail\b|wc\b|tree\b|echo\b|which\b|where\b|type\b"
    r"|Get-ChildItem\b|Get-Content\b|Get-Location\b"
    r"|(node|python|python3|py|java|javac|mvn|gradle|npm|pnpm|yarn|go|cargo|rustc|dotnet) (-v|-V|--version|-version)\s*$"
    r"|npm (ls|list|outdated)\b|pip (list|show|freeze)\b)",
    re.IGNORECASE,
)

# 危险命令：始终要问
DANGEROUS = [
    (re.compile(r"(^|[\s;&|(])(rm|rmdir|del|erase|rd|unlink|shred)\s", re.I), "会删除文件"),
    (re.compile(r"Remove-Item|Clear-Content|\bri\s", re.I), "会删除文件"),
    (re.compile(r"\bfind\b.*\s-(delete|exec)\b", re.I), "find 会删除或执行"),
    (re.compile(r"\bgit\s+push\b", re.I), "会推送到远程仓库"),
    (re.compile(r"\bgit\s+(reset\s+--hard|clean\s+-|checkout\s+(--\s+)?\.(\s|$)|restore\b|stash\s+(drop|clear)|branch\s+-D|rebase\b|filter-branch)", re.I), "会丢掉改动或改写历史"),
    (re.compile(r"\b(npm|pnpm|yarn)\s+publish\b|\btwine\s+upload\b|\bdocker\s+push\b|\bmvn\s+deploy\b", re.I), "会发布到外面"),
    # GitHub 上别人看得到的：开 PR、合并、评论、发版、建仓库、发 issue，以及 gh api 发出写请求
    (re.compile(r"\bgh\s+(pr\s+(create|merge|comment|review|close|edit|ready)|release\s+(create|upload|delete|edit)"
                r"|issue\s+(create|comment|close|edit)|repo\s+(create|delete|edit|fork)|gist\s+create)\b"
                r"|\bgh\s+api\b.*(-X|--method)\s*(POST|PUT|PATCH|DELETE)\b|\bgh\s+api\b.*\s-(f|F)\s", re.I),
     "会在 GitHub 上公开发东西"),
    (re.compile(r"\b(curl|wget|iwr|Invoke-WebRequest|irm|Invoke-RestMethod)\b.*\|\s*(sh|bash|iex|Invoke-Expression|python)\b", re.I), "下载后直接执行"),
    (re.compile(r"\b(iex|Invoke-Expression)\b", re.I), "执行任意字符串"),
    (re.compile(r"\bsudo\b|\brunas\b", re.I), "要管理员权限"),
    # format 只认 Windows 格式化磁盘的写法（format D:），不误伤 git log --format、dotnet format
    (re.compile(r"\b(shutdown|reboot|halt|poweroff|mkfs|diskpart)\b|(^|[\s;&|])format(\.com)?\s+[a-z]:|\bdd\s+if=", re.I), "影响整台电脑"),
    (re.compile(r"\breg\s+(add|delete|import)\b|Set-ItemProperty\s+.*HK(LM|CU)|Set-ExecutionPolicy|\bnetsh\b|\bbcdedit\b|\bsetx\b", re.I), "改系统设置"),
    (re.compile(r"\b(taskkill|Stop-Process|kill|pkill|killall)\b", re.I), "会结束进程"),
    (re.compile(r"\bchmod\s+-R\b|\bchown\b|\bicacls\b|\btakeown\b", re.I), "改文件权限"),
    (re.compile(r"(^|\s)(>|>>)\s*(/etc/|/usr/|C:\\Windows|~/\.)", re.I), "写系统或用户配置文件"),
]


def dangerous(command: str) -> str | None:
    for rx, why in DANGEROUS:
        if rx.search(command):
            return why
    return None


def mentions_secret(command: str) -> bool:
    """命令里提到了像是存着密钥的文件（cat .env 这类）：和直接读那个文件一样要问"""
    if SECRET_DIRS.search(command):
        return True
    for token in re.split(r"[\s'\"=:]+", command):
        name = re.split(r"[/\\]", token)[-1]
        if name and SECRET_NAME.search(name) and not SAFE_ENV_EXAMPLES.match(name):
            return True
    return False


def read_only_command(command: str) -> bool:
    cmd = command.strip()
    return bool(cmd) and not SHELL_META.search(cmd) and not WRITING_OPTIONS.search(cmd) and bool(READ_ONLY_COMMANDS.match(cmd))


class Rules:
    """按项目记住允许过的命令（一字不差地匹配）"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def _load(self) -> dict[str, list[str]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def commands(self, project: Path) -> list[str]:
        return list(self._load().get(str(project), []))

    def all(self) -> dict[str, list[str]]:
        """返回独立的规则副本，查看或修改返回值不会改动记住的命令。"""
        with self._lock:
            return {project: [command for command in commands if isinstance(command, str)]
                    for project, commands in self._load().items() if isinstance(commands, list)}

    def allow(self, project: Path, command: str) -> None:
        with self._lock:
            data = self._load()
            items = data.setdefault(str(project), [])
            if command not in items:
                items.append(command)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def forget(self, project: Path, command: str) -> None:
        with self._lock:
            data = self._load()
            if command in data.get(str(project), []):
                data[str(project)].remove(command)
                self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class McpRules(Protocol):
    """MCP 工具的「以后都不用问」（存在 mcp.json 的 alwaysAllow 里）"""

    def allowed(self, full_name: str) -> bool: ...
    def allow(self, full_name: str) -> None: ...


class Policy:
    def __init__(self, project: Path, rules: Rules, mode: Mode = "ask", mcp: McpRules | None = None) -> None:
        self.project = project
        self.rules = rules
        self.browser_rules = Rules(rules.path.with_name("browser_rules.json"))
        self.mode: Mode = mode
        self.mcp = mcp
        self.edits_allowed = False  # 这个会话里选过「改文件以后都允许」

    def decide(self, kind: str, command: str = "") -> Decision:
        if kind in ("read", "meta", "web"):
            return Decision("allow")
        if kind == "video":
            if self.mode == "read_only":
                return Decision("deny", "现在是只读模式，不能渲染并保存视频。")
            return Decision("ask", "要运行浏览器渲染并保存视频，首次使用可能下载渲染依赖。")
        if kind == "delegate":
            if self.mode == "read_only":
                return Decision("deny", "现在是只读模式，不能派人改代码（可以派 role=review 的审查员）。")
            return Decision("allow")
        if kind == "browser_local":
            if self.mode == "full" or command in self.browser_rules.commands(self.project):
                return Decision("allow")
            return Decision("ask", f"访问本机的 {command} 端口", can_remember=True)
        if kind == "browser_read":
            return Decision("allow")
        if kind in ("browser_get", "browser_interact", "browser_external"):
            if self.mode == "read_only":
                return Decision("deny", "现在是只读模式，只能打开和查看网页、截图、翻页。")
            if (kind == "browser_get" or self.mode == "full"
                    or (kind == "browser_interact" and self.mode == "auto")):
                return Decision("allow")
            reason = "这个网页操作可能会在公网网站上产生变化" if kind == "browser_external" else "要操作本机开发页面"
            return Decision("ask", reason)
        if kind == "secret":
            return Decision("ask", "这个文件可能存着密码或密钥")
        if kind == "edit":
            if self.mode == "read_only":
                return Decision("deny", "现在是只读模式，不能改文件。需要改的话请用户切换模式。")
            if self.mode in AUTO_MODES or self.edits_allowed:
                return Decision("allow")
            return Decision("ask", can_remember=True)
        if kind == "command":
            return self._command(command)
        if kind == "mcp_read":
            return Decision("allow")
        if kind == "mcp":
            # command 这里是工具名（mcp__服务__工具）
            if self.mode == "read_only":
                return Decision("deny", "现在是只读模式，这个外部工具可能会改东西，不能用。")
            if self.mode == "full" or (self.mcp and self.mcp.allowed(command)):
                return Decision("allow")
            return Decision("ask", "这是外部工具（MCP），会在项目以外做事", can_remember=True)
        if kind == "memory":
            if self.mode == "read_only":
                return Decision("deny", "现在是只读模式，不能记项目记忆。")
            if self.mode in AUTO_MODES:
                return Decision("allow")
            return Decision("ask", "记下的话以后每个会话都会带上")
        return Decision("ask")

    def _command(self, command: str) -> Decision:
        why = dangerous(command)
        if why:
            if self.mode == "read_only":
                return Decision("deny", f"现在是只读模式，这条命令{why}，不能执行。")
            return Decision("ask", why)
        if mentions_secret(command):
            if self.mode == "read_only":
                return Decision("deny", "这条命令会读可能存着密钥的文件，只读模式下不执行。")
            return Decision("ask", "这条命令会读可能存着密码或密钥的文件，内容会发给大模型")
        if read_only_command(command):
            return Decision("allow")
        if self.mode == "read_only":
            return Decision("deny", "现在是只读模式，只能执行查看类的命令（git status、ls 这类）。")
        if self.mode in AUTO_MODES or command.strip() in self.rules.commands(self.project):
            return Decision("allow")
        return Decision("ask", can_remember=True)

    def remember(self, kind: str, command: str = "") -> None:
        if kind == "edit":
            self.edits_allowed = True
        elif kind == "command" and command.strip() and not dangerous(command):
            self.rules.allow(self.project, command.strip())
        elif kind == "mcp" and self.mcp:
            self.mcp.allow(command)
        elif kind == "browser_local" and command.isascii() and command.isdecimal() and 0 < int(command) <= 65535:
            self.browser_rules.allow(self.project, command)


COMPUTER_SCOPE = "computer"
COMPUTER_MODES: tuple[Mode, ...] = ("read_only", "ask")
COMPUTER_DANGEROUS = [
    (re.compile(r"\b(Restart-Computer|Stop-Computer|Restart-Service|Stop-Service|Suspend-Computer)\b", re.I), "会关机、重启或停止系统服务"),
    (re.compile(r"\bwinget(?:\.exe)?\s+.*\b(install|uninstall|upgrade|repair)\b", re.I), "会安装、卸载或修改软件"),
    (re.compile(r"\b(choco|scoop)\s+(install|uninstall|update|upgrade|remove)\b", re.I), "会安装、卸载或修改软件"),
    (re.compile(r"\b(msiexec|Install-(Package|Module|Script)|Uninstall-(Package|Module|Script))\b", re.I), "会安装或卸载软件"),
    (re.compile(r"\breg(?:\.exe)?\s+(add|delete|import|restore|load|unload)\b", re.I), "会改注册表"),
    (re.compile(r"\b(Set|New|Remove|Clear)-Item(?:Property)?\b.*(?:HKLM|HKCU|Registry::|HKEY_)", re.I), "会改注册表"),
    (re.compile(r"\b(New|Set|Remove|Enable|Disable|Register|Unregister)-ScheduledTask\b", re.I), "会改计划任务"),
]
COMPUTER_READS = re.compile(
    r"^(Get-(Process|Service|Command|Date|ComputerInfo|CimInstance|Item|ItemProperty|FileHash|Volume|PSDrive)"
    r"|Test-Path|Resolve-Path|where(?:\.exe)?|winget\s+(list|show|search))(?=\s|$)", re.I,
)


def computer_dangerous(command: str) -> str | None:
    why = dangerous(command)
    if why:
        return why
    for pattern, reason in COMPUTER_DANGEROUS:
        if pattern.search(command):
            return reason
    return None


def computer_read_only(command: str) -> bool:
    # PowerShell 的子表达式、调用和脚本块可能藏在查看命令的参数里。
    if SHELL_META.search(command) or re.search(r"[$\x60(){}\[\]]", command):
        return False
    return read_only_command(command) or bool(COMPUTER_READS.match(command.strip()))


class ComputerPolicy(Policy):
    def __init__(self, home: Path, rules: Rules, mode: Mode = "ask", mcp: McpRules | None = None,
                 *, data_dir: Path | None = None, env_file: Path | None = None,
                 remember_commands: bool = True) -> None:
        from .computerpaths import ProtectedPaths

        if mode not in COMPUTER_MODES:
            raise ValueError("电脑任务只有只读和先问再改两种模式。")
        super().__init__(home, rules, mode, mcp)
        self.protected = ProtectedPaths(home, data_dir, env_file)
        self.remember_commands = remember_commands

    def decide(self, kind: str, command: str = "") -> Decision:
        if self.mode not in COMPUTER_MODES:
            return Decision("deny", "电脑任务只有只读和先问再改两种模式。")
        if kind == "secret_edit":
            if self.mode == "read_only":
                return Decision("deny", "现在是只读模式，不能改受保护位置。")
            return Decision("ask", "要改受保护位置，每次都要先确认")
        return super().decide(kind, command)

    def _command(self, command: str) -> Decision:
        readonly = computer_read_only(self.protected.expand(command))
        why = computer_dangerous(command)
        protected = mentions_secret(command) or self.protected.command_touches(command)
        if self.mode == "read_only" and (why or not readonly):
            return Decision("deny", "现在是只读模式，这条命令可能改东西，不能执行。")
        if why or protected:
            return Decision("ask", why or "命令可能访问受保护位置，每次都要先确认")
        if readonly or (self.remember_commands and command.strip() in self.rules.commands(COMPUTER_SCOPE)):  # type: ignore[arg-type]
            return Decision("allow")
        return Decision("ask", can_remember=self.remember_commands)

    def remember(self, kind: str, command: str = "") -> None:
        if kind == "edit":
            self.edits_allowed = True
        elif kind == "command" and command.strip() and self.decide(kind, command).can_remember:
            self.rules.allow(COMPUTER_SCOPE, command.strip())  # type: ignore[arg-type]
