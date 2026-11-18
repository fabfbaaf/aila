"""自定义命令：把常做的流程写成一个文件，以后一句「/名字」她就照着做。

放在哪：
- 项目里的 `.aila/commands/<名字>.md`：跟着项目走，可以提交进仓库给别人用
- 你自己的 `<数据目录>/commands/<名字>.md`：每个项目都能用（电脑任务只用这些）
同名时项目里的优先；和内置命令（/review、/plan……）重名的不算，内置的优先。

文件格式：开头可选一段 `---` 包起来的说明（`description: 一句话`），下面是要交给她的话；
`$ARGUMENTS` 换成命令后面写的内容（没写这个占位、但带了内容时，接在最后）。

为什么在 AI 服务里展开（不在终端、桌面各展开一遍）：几个入口行为一样；会话日志里记的是
「/发布 v1.2」和展开后的全文，界面上能展开看到底发了什么——命令文件是项目里的内容，
你点了才算你说的话，但发了什么要看得见。
"""

import re
from dataclasses import dataclass
from pathlib import Path

NAME = re.compile(r"^[\w一-鿿-]{1,40}$")
MAX_CHARS = 20_000
MAX_COMMANDS = 200
# 内置命令（终端、任务页自己处理的）：同名的自定义命令不认
BUILTIN = {
    "help", "mode", "compact", "new", "resume", "rename", "delete", "usage", "trace", "context", "todo", "changes",
    "init", "plan", "review", "mcp", "memory", "merge", "discard", "rules", "exit", "undo", "budget", "commands",
    "quit", "sessions", "cost", "clear", "commit", "pr", "pr-comments",
}
FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)


@dataclass(frozen=True)
class Command:
    name: str
    description: str
    body: str
    source: str  # project / user
    path: Path

    def expand(self, args: str) -> str:
        args = args.strip()
        if "$ARGUMENTS" in self.body:
            return self.body.replace("$ARGUMENTS", args).strip()
        return f"{self.body.strip()}\n\n补充说明：{args}" if args else self.body.strip()

    def summary(self) -> dict[str, str]:
        return {"name": self.name, "description": self.description, "source": self.source, "path": str(self.path)}


def parse_file(path: Path, source: str) -> Command | None:
    """读一个命令文件；名字不合规、太大、读不了、是内置命令的名字都返回 None"""
    name = path.stem
    if not NAME.fullmatch(name) or name.lower() in BUILTIN:
        return None
    try:
        if path.stat().st_size > MAX_CHARS * 4:
            return None
        text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    except (OSError, UnicodeDecodeError):
        return None
    description = ""
    if m := FRONT.match(text):
        for line in m.group(1).splitlines():
            key, _, value = line.partition(":")
            if key.strip().lower() == "description":
                description = value.strip().strip("\"'")
        text = text[m.end():]
    body = text.strip()[:MAX_CHARS]
    if not body:
        return None
    if not description:
        first = next((line for line in body.splitlines() if line.strip()), "")
        description = first.strip().lstrip("#").strip()[:80]
    return Command(name, description, body, source, path)


def _scan(folder: Path, source: str) -> list[Command]:
    if not folder.is_dir():
        return []
    found = []
    for path in sorted(folder.glob("*.md"))[:MAX_COMMANDS]:
        if path.is_file() and (cmd := parse_file(path, source)):
            found.append(cmd)
    return found


def load_commands(project: Path | None, user_dir: Path) -> dict[str, Command]:
    """名字（小写）→ 命令。项目里的盖过自己的"""
    out = {c.name.lower(): c for c in _scan(user_dir, "user")}
    if project is not None:
        out.update({c.name.lower(): c for c in _scan(project / ".aila" / "commands", "project")})
    return out


def match(text: str, commands: dict[str, Command]) -> tuple[Command, str] | None:
    """「/发布 v1.2」→（发布那个命令，"v1.2"）；不是自定义命令返回 None"""
    if not text.startswith("/") or text.startswith("//"):
        return None
    head, _, args = text[1:].partition(" ")
    head, _, rest = head.partition("\n")  # 「/发布\n多行说明」也认
    cmd = commands.get(head.strip().lower())
    if cmd is None:
        return None
    return cmd, (rest + " " + args).strip() if rest else args
