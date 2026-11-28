"""跑命令。

- 用哪个 shell：AILA_SHELL 指定的；Windows 上装了 Git 就用 Git Bash（大模型最熟 bash 的写法），否则 PowerShell；
  其他系统用 bash
- 每次一个新进程，工作目录是项目根目录；不接标准输入（要交互的命令会直接拿到 EOF，不会卡住）
- 超时杀掉整棵进程树（npm 这类命令会再开子进程）
- 后台命令归会话管理，独立读输出；打断一轮不影响它们，运行时关闭时统一停止
- 输出：标准输出和错误合在一起，太长只留开头和结尾；去掉颜色控制符；UTF-8 解不开就按 GBK（Windows 自带命令）
- 环境变量：去掉艾拉自己的密钥（大模型、搜索），免得一个 `env` 就把密钥读进对话
"""

import asyncio
import codecs
import locale
import os
import re
import shutil
import signal
import subprocess
import sys
from collections.abc import Awaitable, Callable
import time
from dataclasses import dataclass
from pathlib import Path

from .winjobs import WindowsJob

WIN = sys.platform == "win32"
DEFAULT_TIMEOUT = 120.0
MAX_TIMEOUT = 600.0
HEAD_CHARS = 12000
TAIL_CHARS = 12000
JOB_HEAD_CHARS = 4000
JOB_TAIL_CHARS = 20000
JOB_START_WAIT = 3.0
MAX_JOBS = 3
READY = re.compile(rb"ready|listening|started|http://", re.IGNORECASE)
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
    return await run_argv(shell.argv(command), cwd, timeout)


async def run_argv(argv: list[str], cwd: Path, timeout: float = DEFAULT_TIMEOUT,
                   *, env: dict[str, str] | None = None) -> CommandResult:
    """用同一套超时、输出和进程清理逻辑运行固定的程序参数。"""
    timeout = max(1.0, min(float(timeout or DEFAULT_TIMEOUT), MAX_TIMEOUT))
    kwargs: dict = {}
    if WIN:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    t0 = time.perf_counter()
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=env if env is not None else clean_env(),
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


class JobLimitError(Exception):
    """会话的后台任务已经满了，先停掉一个再启动。"""


class JobBuffer:
    """按字保留首尾，分块解码沿用 UTF-8 / GBK 顺序，残字节留到下次输出。"""

    def __init__(self) -> None:
        self.head = ""
        self.tail = ""
        self.total_bytes = 0
        self.total_chars = 0
        self._decoder = codecs.getincrementaldecoder("utf-8")()
        self._ansi = ""
        self._cr = False

    def append(self, chunk: bytes) -> None:
        self.total_bytes += len(chunk)
        pending = self._decoder.getstate()[0]
        try:
            text = self._decoder.decode(chunk)
        except UnicodeDecodeError:
            # 和 decode 一样：UTF-8 不行再试系统编码、GBK。增量解码器保留半个汉字。
            enc = next(iter(_fallback_encodings()), "gbk")
            self._decoder = codecs.getincrementaldecoder(enc)(errors="replace")
            text = self._decoder.decode(pending + chunk)
        self._append_text(text)

    def finish(self) -> None:
        pending = self._decoder.getstate()[0]
        try:
            text = self._decoder.decode(b"", final=True)
        except UnicodeDecodeError:
            text = decode(pending)
        self._append_text(text)

    def _append_text(self, text: str) -> None:
        # 用状态机去控制符：转义序列也可能被拆在两次管道读取之间，不能逐块做正则替换。
        clean: list[str] = []
        for char in text:
            if self._ansi == "esc":
                self._ansi = "csi" if char == "[" else "osc" if char == "]" else ""
            elif self._ansi == "csi":
                if "@" <= char <= "~":
                    self._ansi = ""
            elif self._ansi in ("osc", "osc_esc"):
                if char == "\x07" or (self._ansi == "osc_esc" and char == "\\"):
                    self._ansi = ""
                else:
                    self._ansi = "osc_esc" if char == "\x1b" else "osc"
            elif char == "\x1b":
                self._ansi = "esc"
            else:
                if char != "\n" or not self._cr:
                    clean.append("\n" if char == "\r" else char)
                self._cr = char == "\r"
        text = "".join(clean)
        self.total_chars += len(text)
        room = JOB_HEAD_CHARS - len(self.head)
        self.head += text[:room]
        self.tail = (self.tail + text[room:])[-JOB_TAIL_CHARS:]

    def since(self, offset: int = 0) -> str:
        parts = []
        tail_start = self.total_chars - len(self.tail)
        if offset < len(self.head):
            parts.append(self.head[offset:])
        gap = max(0, tail_start - max(offset, len(self.head)))
        if self.tail and offset < self.total_chars:
            parts.append(self.tail[max(0, offset - tail_start):])
        if gap:
            before = parts[0] if offset < len(self.head) else ""
            after = parts[-1] if self.tail else ""
            return f"{before[:JOB_HEAD_CHARS]}\n{self._omitted()}\n{after[-JOB_TAIL_CHARS:]}"
        return "".join(parts)

    def _omitted(self) -> str:
        return f"…（输出太长，中间省略，共 {self.total_bytes} 字节）…"


class Job:
    """一个后台进程和独立的输出读取任务；查看输出的游标只向前移动。"""

    def __init__(self, id: str, proc: asyncio.subprocess.Process, tree: WindowsJob | None = None) -> None:
        self.id = id
        self.proc = proc
        self._tree = tree
        self._stopped = False
        self.buffer = JobBuffer()
        self.cursor = 0
        self.ready = asyncio.Event()
        self.task = asyncio.create_task(self._pump())

    @property
    def running(self) -> bool:
        if self.task.done():
            return False
        if self.proc.returncode is None:
            return True
        return self._children_running()

    def _children_running(self) -> bool:
        if self._tree:
            return self._tree.running
        if self._stopped:
            return False
        try:
            os.killpg(self.proc.pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    async def _pump(self) -> None:
        assert self.proc.stdout is not None
        probe = b""
        try:
            while chunk := await self.proc.stdout.read(65536):
                self.buffer.append(chunk)
                probe = probe[-128:] + chunk
                if READY.search(probe):
                    self.ready.set()
            self.buffer.finish()
            await self.proc.wait()
            while self._children_running():
                await asyncio.sleep(0.05)  # 子进程可能把输出重定向走了，不能只靠管道判断退出。
        finally:
            if self._tree:
                self._tree.close()
            self.ready.set()  # 短命令退出时也立即返回，不白等三秒。

    def read(self, tail: int | None = None, *, fresh: bool = True) -> str:
        text = self.buffer.since(self.cursor if fresh else 0)
        if fresh:
            self.cursor = self.buffer.total_chars
        if tail is not None:
            text = "\n".join(text.splitlines()[-max(1, min(tail, 1000)):])
        status = "还在跑" if self.running else f"已退出 · 退出码 {self.proc.returncode}"
        body = text.strip() or ("（没有新输出）" if fresh else "（没有输出）")
        return f"{self.id} · {status} · 共输出 {self.buffer.total_bytes} 字节\n{body}"

    async def stop(self) -> None:
        if not self.running and self.task.done():
            return  # 已退出任务的 PID 可能已被系统复用，不能再杀。
        try:
            # POSIX 的进程组可在组长退出后继续存在；Windows 不再对已经退出的 PID 操作。
            if not WIN or self.proc.returncode is None:
                await asyncio.to_thread(kill_tree, self.proc.pid)
        finally:
            if self._tree:
                self._tree.stop()  # shell 提前退出也能收回所有子孙。
            self._stopped = True
        await asyncio.shield(self.task)


class BackgroundJobs:
    """每个会话一份：编号递增、同时最多三个，关闭时停止全部后台进程。"""

    def __init__(self, on_close: Callable[[], Awaitable[None]] | None = None) -> None:
        self.jobs: dict[str, Job] = {}
        self._next = 1
        self._lock = asyncio.Lock()
        self._closed = False
        self._on_close = on_close

    async def start(self, shell: Shell, command: str, cwd: Path) -> Job:
        async with self._lock:
            if self._closed:
                raise JobLimitError("运行时已经关闭，不能再启动后台任务。")
            if sum(job.running for job in self.jobs.values()) >= MAX_JOBS:
                raise JobLimitError("这个会话已经有 3 个后台任务在跑，先用 job_stop 停掉一个。")
            kwargs: dict = {}
            tree = WindowsJob() if WIN else None
            if WIN:
                # CREATE_SUSPENDED：加入 Job Object 后再恢复，保证最早派生的子进程也受控。
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW | 0x00000004
            else:
                kwargs["start_new_session"] = True
            spawn = asyncio.create_task(asyncio.create_subprocess_exec(
                *shell.argv(command), cwd=cwd, env=clean_env(), stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, **kwargs,
            ))
            interrupted = False
            try:
                while True:
                    try:
                        proc = await asyncio.shield(spawn)
                        break
                    except asyncio.CancelledError:
                        # 启动中反复打断也不丢掉进程；登记后再向当轮传播取消。
                        interrupted = True
                if tree:
                    try:
                        tree.attach(proc.pid)
                        tree.resume(proc.pid)
                    except BaseException:
                        tree.stop()
                        await asyncio.to_thread(kill_tree, proc.pid)
                        await proc.wait()
                        raise
            except BaseException:
                if tree:
                    tree.close()
                raise
            job = Job(f"job-{self._next}", proc, tree)
            self._next += 1
            self.jobs[job.id] = job
            if interrupted:
                raise asyncio.CancelledError
        try:
            await asyncio.wait_for(job.ready.wait(), JOB_START_WAIT)
        except TimeoutError:
            pass
        return job

    async def stop_all(self) -> None:
        """会话删除或运行时关闭时停掉进程，并释放挂在会话上的资源。"""
        async with self._lock:
            await asyncio.gather(*(job.stop() for job in self.jobs.values()))
            if self._on_close is not None:
                await self._on_close()

    async def shutdown(self) -> None:
        self._closed = True
        await self.stop_all()


def decode(data: bytes) -> str:
    # 输出被截成两段时，一个汉字可能被切在边上：去掉边上最多 3 个字节能解开，就还是 UTF-8
    for lead in range(4):
        for trail in range(4):
            try:
                return data[lead : len(data) - trail].decode("utf-8")
            except UnicodeDecodeError:
                continue
    # 系统编码（中文 Windows 是 GBK；Python 开了 UTF-8 模式时这里也是 utf-8，所以再试一次 GBK）
    for enc in _fallback_encodings():
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", "replace")


def _fallback_encodings() -> list[str]:
    return [enc for enc in dict.fromkeys([locale.getpreferredencoding(False) or "", "gbk"])
            if enc and enc.lower().replace("-", "") != "utf8"]


def kill_tree(pid: int) -> None:
    try:
        if WIN:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=10)
        else:
            os.killpg(pid, signal.SIGKILL)
    except (OSError, subprocess.TimeoutExpired):
        pass
