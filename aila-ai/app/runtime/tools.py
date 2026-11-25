"""编码工具。都只在项目目录里活动；返回给模型的文字都有长度上限（在这里截好，日志里存的就是截好的，
之后不再改，上下文的开头才稳定）。

每个工具有一个 kind，权限按 kind 判断（见 permissions.py）：
  read     读文件、找文件、搜内容      不用问
  secret   读可能存着密钥的文件        要问（内容会发给大模型）
  edit     改文件、写文件              按权限模式
  command  跑命令                      按权限模式和命令本身
  meta     待办清单                    不用问
  memory   记项目记忆（memory.py）     只读不让记，先问再改要问，自动模式直接记
  mcp      MCP 外部工具（mcp.py）      只读模式不让用，其他模式要问（可以以后都不问）
  mcp_read 服务标了只读的 MCP 工具     不用问
  web      联网搜索、读公网网页（webtools.py）  不用问
  delegate 派子 Agent 在自己的分支里改（workers.py）  只读模式不让派，其他模式不用问（收回改动时按 edit 问）

跑得久的工具（子 Agent）可以用 report_progress 报一句进度：界面上实时显示，不写进日志。
"""

import difflib
import re
from collections.abc import Awaitable, Callable
from contextlib import suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..llm import Usage
from .shell import BackgroundJobs, DEFAULT_TIMEOUT, Job, JobLimitError, MAX_TIMEOUT, Shell, run
from .snapshots import Snapshots
from .workspace import ComputerWorkspace, Workspace, WorkspaceError, decode_text, glob_match, read_text

# 这个工具调用的进度往哪报（loop.py 在开始跑工具前设好；不在工具里调用时什么也不做）
PROGRESS: ContextVar[Callable[[str, str], None] | None] = ContextVar("aila_tool_progress", default=None)


def report_progress(text: str, who: str = "") -> None:
    """跑得久的工具报一句现在在做什么。who：谁在做（比如「1 写 a」这个子 Agent），text：在做什么（「改 a.txt」）。
    分开报是为了英文界面能按模板翻 text"""
    report = PROGRESS.get()
    if report is not None and text.strip():
        report(text.strip()[:200], who.strip()[:60])


READ_LINES = 2000
LINE_CHARS = 2000
READ_CHARS = 60000
LIST_LIMIT = 300
SEARCH_LIMIT = 200
SEARCH_FILE_BYTES = 2_000_000
DIFF_CHARS = 6000
RESULT_DIFF_CHARS = 1500
TODO_STATUS = ("pending", "in_progress", "completed")


@dataclass
class Outcome:
    content: str
    ok: bool = True
    extra: dict[str, Any] = field(default_factory=dict)  # 只给界面看（比如完整的 diff），不发给模型
    # 工具自己调了模型（子 Agent）：用了多少，记进会话的用量
    usage: Usage | None = None


@dataclass(frozen=True)
class _EditPlan:
    """预览只需要文本，实际写入用同一版原始字节留底。"""

    path: Path
    before: str
    after: str
    encoding: str
    original: bytes | None
    secret: bool


@dataclass
class CodingTool:
    name: str
    description: str
    parameters: dict[str, Any]
    run: Callable[[dict[str, Any]], Awaitable[Outcome]]
    kind: Callable[[dict[str, Any]], str]
    status: Callable[[dict[str, Any]], str]
    # 问用户时给他看的内容（由代码算出来，比如 diff），不用大模型自己的描述
    preview: Callable[[dict[str, Any]], str] | None = None
    # 同一步里连着调用几次时可以一起跑（只读、互不影响，比如派几个子 Agent 去查不同的事）
    parallel: bool = False
    # 某些权限按工具算出的目标识别，例如浏览器访问的本机端口。
    permission_key: Callable[[dict[str, Any]], str] | None = None

    @property
    def spec(self) -> dict[str, Any]:
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}


def _obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required}


def _str(desc: str) -> dict[str, str]:
    return {"type": "string", "description": desc}


def _int(desc: str) -> dict[str, str]:
    return {"type": "integer", "description": desc}


class CodingTools:
    def __init__(self, workspace: Workspace, shell: Shell, extra: list[CodingTool] | None = None,
                 snapshots: Snapshots | None = None,
                 on_close: Callable[[], Awaitable[None]] | None = None) -> None:
        self.ws = workspace
        self.shell = shell
        self.snapshots = snapshots
        self.jobs = BackgroundJobs(on_close)
        self.todos: list[dict[str, str]] = []
        self._computer_target: tuple[dict[str, Any], Path, bool] | None = None
        # 基本工具在前、额外的（代码库）在后，顺序固定
        self.tools: dict[str, CodingTool] = {t.name: t for t in [*self._build(), *(extra or [])]}

    @property
    def specs(self) -> list[dict[str, Any]]:
        return [t.spec for t in self.tools.values()]

    def replace_prefixed(self, prefix: str, tools: list[CodingTool]) -> bool:
        """换掉名字以 prefix 开头的那组工具（MCP），放在最后；返回有没有变（变了前缀缓存会断一次）"""
        old = [name for name in self.tools if name.startswith(prefix)]
        new = [t.name for t in tools]
        if old == new and all(self.tools[t.name].spec == t.spec for t in tools):
            return False
        for name in old:
            del self.tools[name]
        for tool in tools:
            self.tools[tool.name] = tool
        return True

    # ---------- 定义（顺序固定：工具列表是请求开头的一部分，变了缓存就不命中） ----------

    def _build(self) -> list[CodingTool]:
        return [
            CodingTool(
                "read_file",
                "读项目里的一个文本文件，每行前面带行号。大文件用 offset、limit 分段读。改文件之前必须先读过。",
                _obj({"path": _str("文件路径（相对项目根目录）"), "offset": _int("从第几行开始（从 1 数），默认 1"),
                      "limit": _int(f"最多读几行，默认 {READ_LINES}")}, ["path"]),
                self._read, self._read_kind, lambda a: f"读 {a.get('path', '')}", self._read_preview,
            ),
            CodingTool(
                "list_files",
                "按文件名模式找文件（遵守 .gitignore）。「*.py」匹配所有目录下的 py 文件，「src/**/*.ts」按路径匹配。",
                _obj({"pattern": _str("文件名模式，默认 *"), "path": _str("只在这个子目录里找（可选）")}, []),
                self._list, self._scope_kind, lambda a: f"找文件 {a.get('pattern') or '*'}", self._read_preview,
            ),
            CodingTool(
                "search",
                "在项目文件内容里按正则表达式搜索，返回 路径:行号: 那一行。files_only=true 时只列出文件。",
                _obj({"pattern": _str("正则表达式"), "path": _str("只在这个子目录里搜（可选）"),
                      "glob": _str("只搜文件名匹配这个模式的文件，比如 *.java（可选）"),
                      "ignore_case": {"type": "boolean", "description": "忽略大小写"},
                      "files_only": {"type": "boolean", "description": "只列出有匹配的文件"},
                      "context": _int("每处匹配前后带几行（0–3），默认 0")}, ["pattern"]),
                self._search, self._scope_kind, lambda a: f"搜「{str(a.get('pattern', ''))[:40]}」", self._read_preview,
            ),
            CodingTool(
                "edit_file",
                "把文件里的一段原文换成新内容。old_string 要和文件里一字不差（含缩进），且只出现一次；"
                "replace_all=true 时替换所有出现的地方。必须先读过这个文件。",
                _obj({"path": _str("文件路径"), "old_string": _str("要换掉的原文"), "new_string": _str("换成的新内容"),
                      "replace_all": {"type": "boolean", "description": "替换所有出现的地方，默认 false"}},
                     ["path", "old_string", "new_string"]),
                self._edit, self._edit_kind, lambda a: f"改 {a.get('path', '')}", self._edit_preview,
            ),
            CodingTool(
                "write_file",
                "写一个文件的全部内容：新建文件，或者整个重写已有文件（重写之前必须读过）。小改动用 edit_file。",
                _obj({"path": _str("文件路径"), "content": _str("文件的全部内容")}, ["path", "content"]),
                self._write, self._edit_kind, lambda a: f"写 {a.get('path', '')}", self._write_preview,
            ),
            CodingTool(
                "run_command",
                "在项目根目录执行一条命令（构建、测试、git 等）。每次都是新的 shell。"
                "开发服务器、watch 等长时间命令用 background=true，返回任务号和启动输出。",
                _obj({"command": _str("要执行的命令"), "description": _str("一句话说明这条命令做什么"),
                      "timeout": _int(f"前台命令的超时秒数，默认 {DEFAULT_TIMEOUT:.0f}，最多 {MAX_TIMEOUT:.0f}"),
                      "background": {"type": "boolean", "description": "放到后台跑，默认 false；一个会话最多同时 3 个"}}, ["command"]),
                self._command, lambda _: "command",
                lambda a: ("后台：" if a.get("background") is True else "")
                + str(a.get("command", "") if isinstance(self.ws, ComputerWorkspace)
                      else a.get("description") or a.get("command", ""))[:60], self._command_preview,
            ),
            CodingTool(
                "todo_write",
                "记录这个任务的步骤和进度。每次传完整的清单；同一时间只有一项是 in_progress。",
                _obj({"items": {"type": "array", "items": _obj(
                    {"content": _str("这一步做什么"), "status": {"type": "string", "enum": list(TODO_STATUS)}},
                    ["content", "status"])}}, ["items"]),
                self._todo, lambda _: "meta", lambda _: "更新待办清单",
            ),
            CodingTool(
                "job_output",
                "看这个会话里后台任务的状态、退出码和上次看过之后的新输出（有上限）。",
                _obj({"id": _str("后台任务号，比如 job-1"), "tail": _int("新输出的最后几行，默认 40，最多 1000")}, ["id"]),
                self._job_output, lambda _: "read", lambda a: f"看后台任务 {a.get('id', '')} 的输出",
            ),
            CodingTool(
                "job_stop",
                "停掉这个会话的后台任务和它开的子进程，返回最后几行输出。用完开发服务器后记得停掉。",
                _obj({"id": _str("后台任务号，比如 job-1")}, ["id"]),
                self._job_stop, lambda _: "meta", lambda a: f"停掉后台任务 {a.get('id', '')}",
            ),
        ]

    # ---------- 读 ----------

    def _read_kind(self, args: dict[str, Any]) -> str:
        if isinstance(self.ws, ComputerWorkspace):
            return self._computer_kind(args)
        try:
            return "secret" if self.ws.is_secret(self.ws.resolve(str(args.get("path", "")))) else "read"
        except WorkspaceError:
            return "read"

    def _scope_kind(self, args: dict[str, Any]) -> str:
        return self._computer_kind(args, default=True) if isinstance(self.ws, ComputerWorkspace) else "read"

    def _edit_kind(self, args: dict[str, Any]) -> str:
        return self._computer_kind(args, write=True) if isinstance(self.ws, ComputerWorkspace) else "edit"

    def _computer_kind(self, args: dict[str, Any], *, write: bool = False, default: bool = False) -> str:
        if self._computer_target is not None and self._computer_target[0] is args:
            # 确认后的权限复查仍绑定第一次展示的目标，不能顺着新链接换一个文件。
            path = self._resolve(args, for_write=write, default=default)
        else:
            self._computer_target = None
            try:
                path = self._resolve(args, for_write=write, default=default)
            except WorkspaceError:
                return "edit" if write else "read"
        raw = Path(str(args.get("path") or path))
        protected = self.ws.is_secret(path) or self.ws.is_secret(raw)
        self._computer_target = (args, path, protected)
        return ("secret_edit" if protected else "edit") if write else ("secret" if protected else "read")

    def _resolve(self, args: dict[str, Any], *, for_write: bool = False, default: bool = False) -> Path:
        raw = str(args.get("path") or "") if default else str(args.get("path", ""))
        path = self.ws.root if default and not raw else self.ws.resolve(raw, for_write=for_write)
        if isinstance(self.ws, ComputerWorkspace) and self._computer_target is not None:
            original_args, original, protected = self._computer_target
            if original_args is args and (
                path != original or protected != (self.ws.is_secret(path) or self.ws.is_secret(Path(raw or path)))
            ):
                raise WorkspaceError("文件路径或保护状态变了，重新确认后再操作。")
        return path

    def _read_preview(self, args: dict[str, Any]) -> str:
        return f"{args.get('path', '')}\n这个文件可能存着密码或密钥；读了以后内容会发给大模型。"

    async def _read(self, args: dict[str, Any]) -> Outcome:
        path = self._resolve(args)
        if not path.is_file():
            raise WorkspaceError(f"{self.ws.rel(path)} 不存在或者不是文件。")
        text, _ = read_text(path)
        self.ws.remember(path)
        lines = text.splitlines()
        if not lines:
            return Outcome("（空文件）")
        offset = max(1, int(args.get("offset") or 1))
        limit = max(1, min(int(args.get("limit") or READ_LINES), READ_LINES))
        if offset > len(lines):
            return Outcome(f"文件只有 {len(lines)} 行。", ok=False)
        out: list[str] = []
        size = 0
        end = offset - 1
        for i in range(offset - 1, min(len(lines), offset - 1 + limit)):
            line = lines[i]
            if len(line) > LINE_CHARS:
                line = line[:LINE_CHARS] + "…（这一行太长，后面省略）"
            row = f"{i + 1:>6}\t{line}"
            if size + len(row) > READ_CHARS:
                break
            out.append(row)
            size += len(row) + 1
            end = i + 1
        if end < len(lines):
            out.append(f"…（共 {len(lines)} 行，读到第 {end} 行；用 offset={end + 1} 接着读）")
        return Outcome("\n".join(out))

    async def _list(self, args: dict[str, Any]) -> Outcome:
        base = self._resolve(args, default=True)
        if not base.is_dir():
            raise WorkspaceError(f"{self.ws.rel(base)} 不是目录。")
        pattern = str(args.get("pattern") or "*")
        rels = sorted(r for r in (self.ws.rel(p) for p in self.ws.files(base)) if glob_match(r, pattern))
        if not rels:
            return Outcome(f"没有匹配「{pattern}」的文件。")
        shown = rels[:LIST_LIMIT]
        more = f"\n…还有 {len(rels) - LIST_LIMIT} 个，换个更具体的模式" if len(rels) > LIST_LIMIT else ""
        return Outcome(f"共 {len(rels)} 个文件：\n" + "\n".join(shown) + more)

    async def _search(self, args: dict[str, Any]) -> Outcome:
        flags = re.IGNORECASE if args.get("ignore_case") else 0
        try:
            rx = re.compile(str(args.get("pattern", "")), flags)
        except re.error as err:
            return Outcome(f"正则表达式写错了：{err}", ok=False)
        base = self._resolve(args, default=True)
        glob = str(args.get("glob") or "")
        context = max(0, min(int(args.get("context") or 0), 3))
        files_only = bool(args.get("files_only"))

        hits: list[str] = []
        files: list[str] = []
        count = 0
        explicit_protected = isinstance(self.ws, ComputerWorkspace) and self.ws.is_secret(base)
        candidates = [base] if base.is_file() else self.ws.files(base)
        for path in candidates:
            rel = self.ws.rel(path)
            if (glob and not glob_match(rel, glob)) or (self.ws.is_secret(path) and not explicit_protected):
                continue
            try:
                if path.stat().st_size > SEARCH_FILE_BYTES:
                    continue
                raw = path.read_bytes()
            except OSError:
                continue
            if b"\0" in raw[:8192]:
                continue
            lines = raw.decode("utf-8", "replace").splitlines()
            matched = [i for i, line in enumerate(lines) if rx.search(line)]
            if not matched:
                continue
            files.append(rel)
            count += len(matched)
            if files_only:
                continue
            shown: set[int] = set()
            for i in matched:
                for j in range(max(0, i - context), min(len(lines), i + context + 1)):
                    if j in shown:
                        continue
                    shown.add(j)
                    sep = ":" if j == i else "-"
                    hits.append(f"{rel}{sep}{j + 1}{sep} {lines[j][:300]}")
                if len(hits) >= SEARCH_LIMIT:
                    break
            if len(hits) >= SEARCH_LIMIT:
                break
        if not files:
            return Outcome("没有搜到。")
        if files_only:
            return Outcome(f"{len(files)} 个文件里有：\n" + "\n".join(files[:LIST_LIMIT]))
        more = f"\n…只列出前 {SEARCH_LIMIT} 行，缩小范围再搜" if len(hits) >= SEARCH_LIMIT else ""
        return Outcome(f"{count} 处，在 {len(files)} 个文件里：\n" + "\n".join(hits[:SEARCH_LIMIT]) + more)

    # ---------- 改 ----------

    def _plan_edit(self, args: dict[str, Any]) -> _EditPlan:
        raw = str(args.get("path", ""))
        path = self._resolve(args, for_write=True)
        old, new = str(args.get("old_string", "")), str(args.get("new_string", ""))
        if not path.is_file():
            raise WorkspaceError(f"{self.ws.rel(path)} 不存在；新建文件用 write_file。")
        if not old:
            raise WorkspaceError("old_string 不能为空；要整个重写用 write_file。")
        original = self.ws.check_fresh(path)
        text, encoding = decode_text(original, path.name)
        crlf = "\r\n" in text
        old_n, new_n = _eol(old, crlf), _eol(new, crlf)
        count = text.count(old_n)
        if count == 0:
            raise WorkspaceError(f"{self.ws.rel(path)} 里找不到 old_string。要和文件一字不差（包括缩进和空格），先重新读一下那几行。")
        if count > 1 and not args.get("replace_all"):
            raise WorkspaceError(f"old_string 在 {self.ws.rel(path)} 里出现了 {count} 次：多带几行上下文让它只出现一次，或者用 replace_all。")
        after = text.replace(old_n, new_n) if args.get("replace_all") else text.replace(old_n, new_n, 1)
        if after == text:
            raise WorkspaceError("改完和原来一样，没有要改的。")
        secret = self.ws.is_secret(path) or self.ws.is_secret(Path(raw.strip()))
        return _EditPlan(path, text, after, encoding, original, secret)

    def _edit_preview(self, args: dict[str, Any]) -> str:
        try:
            plan = self._plan_edit(args)
        except WorkspaceError as err:
            return str(err)
        return _cap(make_diff(self.ws.rel(plan.path), plan.before, plan.after), DIFF_CHARS)

    async def _edit(self, args: dict[str, Any]) -> Outcome:
        return self._apply_edit(self._plan_edit(args))

    def _plan_write(self, args: dict[str, Any]) -> _EditPlan:
        raw = str(args.get("path", ""))
        path = self._resolve(args, for_write=True)
        secret = self.ws.is_secret(path) or self.ws.is_secret(Path(raw.strip()))
        content = str(args.get("content", ""))
        if path.is_dir():
            raise WorkspaceError(f"{self.ws.rel(path)} 是目录。")
        if path.exists():
            original = self.ws.check_fresh(path)
            before, encoding = decode_text(original, path.name)
            if "\r\n" in before:
                content = _eol(content, True)
            return _EditPlan(path, before, content, encoding, original, secret)
        return _EditPlan(path, "", content, "utf-8", None, secret)

    def _write_preview(self, args: dict[str, Any]) -> str:
        try:
            plan = self._plan_write(args)
        except WorkspaceError as err:
            return str(err)
        return _cap(make_diff(self.ws.rel(plan.path), plan.before, plan.after), DIFF_CHARS)

    async def _write(self, args: dict[str, Any]) -> Outcome:
        return self._apply_edit(self._plan_write(args))

    def _apply_edit(self, plan: _EditPlan) -> Outcome:
        path = plan.path
        data = plan.after.encode(plan.encoding)
        created = plan.original is None
        snapshots = None if plan.secret else self.snapshots
        metadata = snapshots.capture(plan.original, data) if snapshots is not None else {}
        writing = False
        try:
            if self.ws.resolve(str(path), for_write=True) != path:
                raise WorkspaceError("文件指向的位置变了，先重新读一下再改。")
            path.parent.mkdir(parents=True, exist_ok=True)
            # 文件恰好被用户新建时不能覆盖。已有文件用同一个句柄再检查和写，保留原来的权限。
            with path.open("xb" if created else "r+b") as stream:
                if not created and snapshots is not None:
                    if stream.read() != plan.original:
                        raise WorkspaceError("文件在准备写入时被改过，先重新读一下再改。")
                    stream.seek(0)
                writing = True
                stream.write(data)
                stream.truncate()
        except (OSError, WorkspaceError):
            index = metadata.get("snap")
            if not writing and snapshots is not None and type(index) is int:
                with suppress(OSError, WorkspaceError):
                    snapshots.discard(index)
            raise
        self.ws.remember(path, data)
        result = self._changed(path, plan.before, plan.after, created=created)
        result.extra.update(metadata)
        return result

    def _changed(self, path: Path, before: str, after: str, created: bool = False) -> Outcome:
        rel = self.ws.rel(path)
        diff = make_diff(rel, before, after)
        added = sum(1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
        removed = sum(1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---"))
        head = f"{'新建了' if created else '改好了'} {rel}（+{added} −{removed}）"
        return Outcome(f"{head}\n{_cap(diff, RESULT_DIFF_CHARS)}", extra={"diff": _cap(diff, DIFF_CHARS), "file": rel})

    # ---------- 命令 ----------

    def _command_preview(self, args: dict[str, Any]) -> str:
        desc = "" if isinstance(self.ws, ComputerWorkspace) else str(args.get("description") or "").strip()
        return f"{args.get('command', '')}" + (f"\n（{desc}）" if desc else "") + f"\n在 {self.ws.root} 里执行" + (
            "（后台）" if args.get("background") is True else ""
        )

    async def _command(self, args: dict[str, Any]) -> Outcome:
        command = str(args.get("command", "")).strip()
        if not command:
            return Outcome("没给命令。", ok=False)
        if not isinstance(args.get("background", False), bool):
            return Outcome("background 要是 true 或 false。", ok=False)
        if args.get("background"):
            try:
                job = await self.jobs.start(self.shell, command, self.ws.root)
            except JobLimitError as err:
                return Outcome(str(err), ok=False)
            return self._job_result(job, job.read())
        timeout = float(args.get("timeout") or DEFAULT_TIMEOUT)
        result = await run(self.shell, command, self.ws.root, timeout)
        return Outcome(result.render(timeout), ok=result.exit_code == 0, extra={"exit_code": result.exit_code})

    def _job_result(self, job: Job, content: str) -> Outcome:
        return Outcome(content, extra={
            "job_id": job.id, "exit_code": None if job.running else job.proc.returncode,
            "total_bytes": job.buffer.total_bytes,
        })

    async def _job_output(self, args: dict[str, Any]) -> Outcome:
        job = self.jobs.jobs.get(str(args.get("id", "")))
        if job is None:
            return Outcome(f"这个会话里没有后台任务 {args.get('id', '')}。先用 run_command 的 background=true 启动。", ok=False)
        try:
            tail = int(args.get("tail", 40))
        except (TypeError, ValueError):
            return Outcome("tail 要是行数，比如 40。", ok=False)
        return self._job_result(job, job.read(tail))

    async def _job_stop(self, args: dict[str, Any]) -> Outcome:
        job = self.jobs.jobs.get(str(args.get("id", "")))
        if job is None:
            return Outcome(f"这个会话里没有后台任务 {args.get('id', '')}。", ok=False)
        await job.stop()
        return self._job_result(job, job.read(40, fresh=False))

    # ---------- 待办 ----------

    async def _todo(self, args: dict[str, Any]) -> Outcome:
        items = args.get("items")
        if not isinstance(items, list):
            return Outcome("items 要是一个列表。", ok=False)
        clean = []
        for item in items[:30]:
            if isinstance(item, dict) and str(item.get("content", "")).strip():
                status = item.get("status") if item.get("status") in TODO_STATUS else "pending"
                clean.append({"content": str(item["content"]).strip()[:200], "status": status})
        self.todos = clean
        marks = {"pending": "[ ]", "in_progress": "[→]", "completed": "[x]"}
        text = "\n".join(f"{marks[i['status']]} {i['content']}" for i in clean) or "（清单是空的）"
        return Outcome(f"待办清单已更新：\n{text}", extra={"todos": clean})


def _eol(text: str, crlf: bool) -> str:
    text = text.replace("\r\n", "\n")
    return text.replace("\n", "\r\n") if crlf else text


def make_diff(rel: str, before: str, after: str) -> str:
    a = before.replace("\r\n", "\n").splitlines(keepends=True)
    b = after.replace("\r\n", "\n").splitlines(keepends=True)
    lines = difflib.unified_diff(a, b, fromfile=f"a/{rel}" if before else "/dev/null", tofile=f"b/{rel}", n=3)
    return "".join(line if line.endswith("\n") else line + "\n" for line in lines)


def _cap(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "\n…（diff 太长，后面省略）"
