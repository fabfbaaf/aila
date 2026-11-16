"""权限：每次工具调用之前决定 允许 / 先问用户 / 拒绝。

三种模式（会话里随时可以换）：
  read_only  只读：不改文件；命令只允许明显只读的那些（git status、ls……）
  ask        先问再改（默认）：读不用问；改文件、跑命令要问，明显只读的命令和你允许过的命令不用问
  auto       工作区内自动：改文件、跑命令都不问，危险命令除外

不管哪种模式：
- 危险命令（删除、强推、改系统设置、下载就执行……）始终要问，也不能「以后都允许」
- 读可能存着密钥的文件要问
- 问的时候可以选「以后都允许」：改文件 → 这个会话里改文件不再问；命令 → 这个项目里这条命令（一字不差）以后不再问，
  记在 data/agent_rules.json

Windows 上没有沙箱：命令真正能做什么只能靠上面这些规则把关。危险命令的识别是尽力而为，不是保证。
"""

import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .workspace import SAFE_ENV_EXAMPLES, SECRET_NAME

Mode = Literal["read_only", "ask", "auto"]
MODES: tuple[Mode, ...] = ("read_only", "ask", "auto")
MODE_LABELS = {"read_only": "只读", "ask": "先问再改", "auto": "工作区内自动"}


@dataclass(frozen=True)
class Decision:
    verdict: Literal["allow", "ask", "deny"]
    reason: str = ""
    can_remember: bool = False


# 命令里有这些就不算「明显只读」：可能串了别的命令、重定向写文件、或者展开出别的东西
SHELL_META = re.compile(r"[;&|<>`\n\r]|\$\(")

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
    (re.compile(r"\b(curl|wget|iwr|Invoke-WebRequest|irm|Invoke-RestMethod)\b.*\|\s*(sh|bash|iex|Invoke-Expression|python)\b", re.I), "下载后直接执行"),
    (re.compile(r"\b(iex|Invoke-Expression)\b", re.I), "执行任意字符串"),
    (re.compile(r"\bsudo\b|\brunas\b", re.I), "要管理员权限"),
    (re.compile(r"\b(shutdown|reboot|halt|poweroff|mkfs|diskpart|format)\b|\bdd\s+if=", re.I), "影响整台电脑"),
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
    for token in re.split(r"[\s'\"=:]+", command):
        name = re.split(r"[/\\]", token)[-1]
        if name and SECRET_NAME.search(name) and not SAFE_ENV_EXAMPLES.match(name):
            return True
    return False


def read_only_command(command: str) -> bool:
    cmd = command.strip()
    return bool(cmd) and not SHELL_META.search(cmd) and bool(READ_ONLY_COMMANDS.match(cmd))


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


class Policy:
    def __init__(self, project: Path, rules: Rules, mode: Mode = "ask") -> None:
        self.project = project
        self.rules = rules
        self.mode: Mode = mode
        self.edits_allowed = False  # 这个会话里选过「改文件以后都允许」

    def decide(self, kind: str, command: str = "") -> Decision:
        if kind in ("read", "meta"):
            return Decision("allow")
        if kind == "secret":
            return Decision("ask", "这个文件可能存着密码或密钥")
        if kind == "edit":
            if self.mode == "read_only":
                return Decision("deny", "现在是只读模式，不能改文件。需要改的话请用户切换模式。")
            if self.mode == "auto" or self.edits_allowed:
                return Decision("allow")
            return Decision("ask", can_remember=True)
        if kind == "command":
            return self._command(command)
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
        if self.mode == "auto" or command.strip() in self.rules.commands(self.project):
            return Decision("allow")
        return Decision("ask", can_remember=True)

    def remember(self, kind: str, command: str = "") -> None:
        if kind == "edit":
            self.edits_allowed = True
        elif kind == "command" and command.strip() and not dangerous(command):
            self.rules.allow(self.project, command.strip())
