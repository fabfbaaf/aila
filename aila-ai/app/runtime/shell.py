"""跑命令。

- 用哪个 shell：AILA_SHELL 指定的；Windows 上装了 Git 就用 Git Bash（大模型最熟 bash 的写法），否则 PowerShell；
  其他系统用 bash
- 每次一个新进程，工作目录是项目根目录；不接标准输入（要交互的命令会直接拿到 EOF，不会卡住）
- 超时杀掉整棵进程树（npm 这类命令会再开子进程）
- 输出：标准输出和错误合在一起，太长只留开头和结尾；去掉颜色控制符；UTF-8 解不开就按 GBK（Windows 自带命令）
- 环境变量：去掉艾拉自己的密钥（大模型、搜索），免得一个 `env` 就把密钥读进对话
"""

import asyncio
import locale
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

WIN = sys.platform == "win32"
DEFAULT_TIMEOUT = 120.0
MAX_TIMEOUT = 600.0
HEAD_CHARS = 12000
TAIL_CHARS = 12000
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")
# 艾拉自己的配置里的密钥：不传给她跑的命令
OWN_SECRETS = ("LLM_API_KEY", "SEARCH_API_KEY", "AILA_ACCOUNT_TOKEN")


@dataclass(frozen=True)
class Shell:
    kind: str  # bash / powershell
    exe: str

    @property
    def label(self) -> str:
        return "Git Bash" if self.kind == "bash" and WIN else "bash" if self.kind == "bash" else "PowerShell"

    def argv(self, command: str) -> list[str]:
        if self.kind == "powershell":
            return [self.exe, "-NoProfile", "-NonInteractive", "-Command", command]
        return [self.exe, "-c", command]


def find_shell() -> Shell:
    custom = os.getenv("AILA_SHELL", "").strip()
    if custom:
        kind = "powershell" if "powershell" in custom.lower() or custom.lower().endswith("pwsh.exe") else "bash"
        return Shell(kind, custom)
    if not WIN:
        return Shell("bash", shutil.which("bash") or "/bin/sh")
    for candidate in _git_bash_candidates():
        if candidate.is_file():
            return Shell("bash", str(candidate))
    return Shell("powershell", shutil.which("powershell") or "powershell.exe")


def _git_bash_candidates() -> list[Path]:
    found = []
    git = shutil.which("git")
    if git:
        # ...\Git\cmd\git.exe → ...\Git\bin\bash.exe
        found.append(Path(git).resolve().parent.parent / "bin" / "bash.exe")
    for base in (os.getenv("ProgramFiles", r"C:\Program Files"), os.getenv("ProgramFiles(x86)", "")):
        if base:
            found.append(Path(base) / "Git" / "bin" / "bash.exe")
    return found


@dataclass
class CommandResult:
    exit_code: int | None  # None：超时被杀
    output: str
    seconds: float
    truncated: bool

    def render(self, timeout: float) -> str:
        status = f"超时（{timeout:.0f} 秒）被停止" if self.exit_code is None else f"退出码 {self.exit_code}"
        body = self.output.strip() or "（没有输出）"
        return f"{status} · 用时 {self.seconds:.1f} 秒\n{body}"


def clean_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in OWN_SECRETS}
    env.update({
        "GIT_PAGER": "cat", "PAGER": "cat", "GIT_TERMINAL_PROMPT": "0",
        "NO_COLOR": "1", "FORCE_COLOR": "0", "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1",
    })
    return env


async def run(shell: Shell, command: str, cwd: Path, timeout: float = DEFAULT_TIMEOUT) -> CommandResult:
    timeout = max(1.0, min(float(timeout or DEFAULT_TIMEOUT), MAX_TIMEOUT))
    kwargs: dict = {}
    if WIN:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    t0 = time.perf_counter()
    proc = await asyncio.create_subprocess_exec(
        *shell.argv(command),
        cwd=cwd,
        env=clean_env(),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        **kwargs,
    )
    head = bytearray()
    tail = bytearray()
    total = 0

    async def pump() -> None:
        nonlocal total
        assert proc.stdout is not None
        while chunk := await proc.stdout.read(65536):
            total += len(chunk)
            room = HEAD_CHARS * 3 - len(head)
            if room > 0:
                head.extend(chunk[:room])
                chunk = chunk[room:]
            if chunk:
                tail.extend(chunk)
                del tail[: max(0, len(tail) - TAIL_CHARS * 3)]

    exit_code: int | None
    try:
        await asyncio.wait_for(asyncio.gather(pump(), proc.wait()), timeout)
        exit_code = proc.returncode
    except TimeoutError:
        kill_tree(proc.pid)
        await proc.wait()
        exit_code = None
    except asyncio.CancelledError:
        kill_tree(proc.pid)  # 被用户打断：命令也一起停
        raise
    seconds = time.perf_counter() - t0

    text_head, text_tail = decode(bytes(head)), decode(bytes(tail))
    truncated = total > len(head) + len(tail) or len(text_head) > HEAD_CHARS or len(text_tail) > TAIL_CHARS
    if truncated:
        output = f"{text_head[:HEAD_CHARS]}\n…（输出太长，中间省略，共 {total} 字节）…\n{text_tail[-TAIL_CHARS:]}"
    else:
        output = text_head + text_tail
    return CommandResult(exit_code, ANSI.sub("", output).replace("\r\n", "\n"), seconds, truncated)


def decode(data: bytes) -> str:
    # 输出被截成两段时，一个汉字可能被切在边上：去掉边上最多 3 个字节能解开，就还是 UTF-8
    for lead in range(4):
        for trail in range(4):
            try:
                return data[lead : len(data) - trail].decode("utf-8")
            except UnicodeDecodeError:
                continue
    # 系统编码（中文 Windows 是 GBK；Python 开了 UTF-8 模式时这里也是 utf-8，所以再试一次 GBK）
    for enc in dict.fromkeys([locale.getpreferredencoding(False) or "", "gbk"]):
        if not enc or enc.lower().replace("-", "") == "utf8":
            continue
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", "replace")


def kill_tree(pid: int) -> None:
    try:
        if WIN:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=10)
        else:
            os.killpg(pid, signal.SIGKILL)
    except (OSError, subprocess.TimeoutExpired):
        pass
