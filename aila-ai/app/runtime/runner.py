"""运行时：管理所有会话，每个会话的一轮在后台任务里跑。

- 任务不依附于某个连接：桌面窗口关了、终端断了，她照样做完（要问的事等你回来回答，超时就当没同意）
- 订阅：先补发 after 之后的日志事件，再接着推实时事件（包括逐字的文字）；桌面和终端可以同时看同一个会话
- 一个会话同一时间只跑一轮；打断 = 取消后台任务，循环自己把日志补完整
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from ..code import CodeProjects
from ..code.fix import FixError, git
from ..confirm import Confirmations
from ..llm import CoderModel
from .branch import BranchError, BranchInfo, open_branch
from .branch import discard as discard_branch
from .branch import merge as merge_branch
from .codetools import CodeAccess
from .ledger import context_ledger
from .memory import ProjectMemory, remember_tool
from .loop import Agent, AgentConfig, Hooks
from .permissions import MODES, Mode, Policy, Rules
from .pricing import validate_budget
from .prompt import build_context
from .review import ReviewError, review_request, review_title
from .session import Event, Session, SessionStore, latest_turn, turn_edits
from .shell import Shell, find_shell
from .snapshots import Snapshots
from .subagent import Explorer
from .tools import CodingTools
from .workspace import Workspace

log = logging.getLogger("aila.runtime")

# 编程任务里问用户的事可能要等很久（人走开了），比对话里的两分钟长得多
CONFIRM_TIMEOUT = 1800.0
BRANCH_NOTE = (
    "\n\n<分支>\n你在新分支 {branch} 上工作（git worktree 临时目录，就是上面的项目目录）。"
    "这里的改动不会直接影响用户的代码（{project}）：用户确认后才会跑测试、合并回去。"
    "不要自己合并、推送或切换分支。\n</分支>"
)


class RuntimeError_(Exception):
    """说给调用方（接口、终端）听的错误"""


class Runtime:
    def __init__(
        self,
        data_dir: Path,
        model_factory: Callable[[], CoderModel],
        confirms: Confirmations | None = None,
        shell: Shell | None = None,
        config: AgentConfig | None = None,
        code_projects: Callable[[], CodeProjects] | None = None,
        fast_factory: Callable[[], CoderModel] | None = None,
    ) -> None:
        self.store = SessionStore(data_dir / "sessions")
        # 「在新分支上做」的临时目录（git worktree）
        self.work_dir = data_dir / "worktrees"
        # 代码库（代码 RAG + 调用图）：给了就让她能按意思搜、按报错定位、看调用关系
        self.code_projects = code_projects
        self.rules = Rules(data_dir / "agent_rules.json")
        # 项目记忆：她学到的项目约定，下次在这个项目开会话时放进环境说明
        self.memory = ProjectMemory(data_dir / "project_memory.json")
        self.model_factory = model_factory
        # 「快模型」：派出去查资料的子 Agent 用（explore 工具），没给就和主模型一样
        self.fast_factory = fast_factory or model_factory
        self.confirms = confirms or Confirmations(timeout=CONFIRM_TIMEOUT)
        self.shell = shell or find_shell()
        self.config = config or AgentConfig()
        self.hooks = Hooks()
        self._agents: dict[str, Agent] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._listeners: dict[str, set[asyncio.Queue[Event | None]]] = {}
        self._deleting: set[str] = set()
        self._operations: set[str] = set()
        # 正在 /review 的会话 → 这一轮结束后要换回的权限模式
        self._reviewing: dict[str, Mode] = {}

    # ---------- 会话 ----------

    def create(self, cwd: str, mode: Mode = "ask", isolate: bool = False,
               budget: float | None = None) -> Session:
        """isolate：在新分支上做（git worktree），合并之前你的代码一个字都不动"""
        try:
            budget = validate_budget(budget)
        except ValueError as err:
            raise RuntimeError_(str(err)) from err
        root = Path(cwd).expanduser()
        if not root.is_dir():
            raise RuntimeError_(f"{cwd} 不是一个目录。")
        if mode not in MODES:
            raise RuntimeError_(f"不认识的权限模式：{mode}")
        root = root.resolve()
        model = self.model_factory()
        if not isolate:
            session = self.store.create(root, mode, model.name, build_context(root, self.shell.label) + self.memory.context(root))
        else:
            try:
                info = open_branch(root, self.work_dir)
            except BranchError as err:
                raise RuntimeError_(str(err)) from err
            context = (build_context(info.workdir, self.shell.label) + BRANCH_NOTE.format(branch=info.branch, project=root)
                       + self.memory.context(root))
            session = self.store.create(root, mode, model.name, context, {"branch": asdict(info)})
        if budget is not None:
            session.append({"type": "budget", "amount": budget})
        return session

    def agent(self, sid: str) -> Agent:
        if sid in self._deleting:
            raise RuntimeError_("这个会话正在删除，等删完再开新会话。")
        if sid in self._agents:
            return self._agents[sid]
        session = self.store.get(sid)
        if session is None:
            raise RuntimeError_("没有这个会话。")
        # 在新分支上做时，她在临时目录里干活；代码库索引、记住的命令还是按你原来的项目
        workspace = Workspace(session.workdir if session.workdir.is_dir() else session.cwd)
        mode = session.mode if session.mode in MODES else "ask"
        extra = CodeAccess(self.code_projects(), session.cwd).tools() if self.code_projects else []
        explorer = Explorer(session, workspace.root, self.shell, extra, self.rules, self.fast_factory,
                            self.config.context_window)
        agent = Agent(
            session,
            self.model_factory(),
            # 记忆按你原来的项目记（在新分支上做时也是）
            CodingTools(workspace, self.shell, [*extra, explorer.tool(), remember_tool(self.memory, session.cwd, session.id)],
                        snapshots=Snapshots(session.path.with_suffix(".snap"))),
            Policy(session.cwd, self.rules, mode),  # type: ignore[arg-type]
            self.confirms,
            self.config,
            self.hooks,
        )
        if _unfinished(session):
            # 上次做到一半服务就停了（被强制关掉、电脑重启）：补完整，记成被打断，接着说就能接着做
            agent.repair()
        self._agents[sid] = agent
        return agent

    def running(self, sid: str) -> bool:
        task = self._tasks.get(sid)
        return task is not None and not task.done()

    def set_mode(self, sid: str, mode: Mode) -> None:
        if mode not in MODES:
            raise RuntimeError_(f"不认识的权限模式：{mode}")
        agent = self.agent(sid)
        if sid in self._reviewing:
            # /review 这一轮必须只读：先记下，这一轮结束后再换
            self._reviewing[sid] = mode
        else:
            agent.policy.mode = mode
        self._publish(sid, agent.session.append({"type": "mode", "mode": mode}))

    def set_budget(self, sid: str, amount: float | None) -> Event:
        """改上限只追加事件；正在执行的一步结束后，下一步会看到新上限。"""
        try:
            amount = validate_budget(amount)
        except ValueError as err:
            raise RuntimeError_(str(err)) from err
        if sid in self._deleting:
            raise RuntimeError_("这个会话正在删除，不能改上限。")
        session = self.store.get(sid)
        if session is None:
            raise RuntimeError_("没有这个会话。")
        event = session.append({"type": "budget", "amount": amount})
        self._publish(sid, event)
        return event

    def rename(self, sid: str, title: str) -> Session:
        """改名只追加展示事件，不重写历史，也不改变模型的上下文。"""
        if sid in self._deleting:
            raise RuntimeError_("这个会话正在删除，不能改名。")
        session = self.store.get(sid)
        if session is None:
            raise RuntimeError_("没有这个会话。")
        if not title.strip() or not 1 <= len(title) <= 100:
            raise RuntimeError_("标题要有 1–100 个字。")
        title = title.strip()
        self._publish(sid, session.append({"type": "title", "title": title}))
        return session

    async def delete(self, sid: str) -> None:
        """先停掉会话的后台进程、放弃分支，再移除日志和订阅；不打断正在做的轮次。"""
        if sid in self._deleting:
            raise RuntimeError_("这个会话正在删除，等删完再开新会话。")
        session = self.store.get(sid)
        if session is None:
            raise RuntimeError_("没有这个会话。")
        if self.running(sid):
            raise RuntimeError_("她还在做事，先打断这一轮再删除。")
        if sid in self._operations:
            raise RuntimeError_("这个会话正在合并、放弃分支或压缩，等它结束再删除。")
        self._deleting.add(sid)
        try:
            agent = self._agents.get(sid)
            if agent is not None:
                await agent.tools.jobs.stop_all()
            if info := session.branch:
                await asyncio.to_thread(discard_branch, BranchInfo.of(info))
                if Path(info["worktree"]).exists():
                    raise RuntimeError_("分支的临时目录还在，没删会话。关闭占用它的程序后再试。")
                ref = f"refs/heads/{info['branch']}"
                refs = await asyncio.to_thread(git, Path(info["repo"]), "for-each-ref", "--format=%(refname)", ref)
                if ref in refs.splitlines():
                    raise RuntimeError_("任务的 Git 分支还在，没删会话。确认它没有在别的工作区使用后再试。")
                self._publish(sid, session.append({"type": "branch_closed", "branch": info["branch"]}))
            self.store.delete(sid)
            self._agents.pop(sid, None)
            self._tasks.pop(sid, None)
            for queue in self._listeners.pop(sid, set()):
                queue.put_nowait(None)
        except (BranchError, FixError, OSError) as err:
            raise RuntimeError_(f"会话没删掉：{err}") from err
        finally:
            self._deleting.discard(sid)

    # ---------- 跑一轮 ----------

    def run(self, sid: str, text: str, review: bool = False) -> AsyncIterator[Event]:
        """开始一轮，返回这一轮的事件流（先订阅再开始，一个字都不会漏）。

        review=True：/review，只读地审一遍没提交的改动；text 是可选的「特别注意什么」。
        """
        agent, request, extra = self._ready(sid, text, review)
        stream = self.subscribe(sid, len(agent.session.events) - 1)
        self._begin(sid, agent, request, extra)
        return stream

    def start(self, sid: str, text: str) -> None:
        """开始一轮，不订阅（从对话、屏幕上的报错、星图交过来的任务：工作窗口打开后自己接上）"""
        agent, request, extra = self._ready(sid, text)
        self._begin(sid, agent, request, extra)

    def _ready(self, sid: str, text: str, review: bool = False) -> tuple[Agent, str, dict]:
        agent = self.agent(sid)
        if self.running(sid):
            raise RuntimeError_("她还在做上一件事：等她做完，或者先打断。")
        if sid in self._operations:
            raise RuntimeError_("这个会话正在合并、放弃分支或压缩，等它结束再说。")
        if not review and not text.strip():
            raise RuntimeError_("要做什么？")
        if agent.session.branch_closed:
            raise RuntimeError_("这个会话的分支已经放弃了。开一个新任务吧。")
        if not review:
            return agent, text, {}
        # 在新分支上做时审的是分支临时目录里的改动
        root = agent.session.workdir if agent.session.workdir.is_dir() else agent.session.cwd
        try:
            request = review_request(root, text)
        except ReviewError as err:
            raise RuntimeError_(str(err)) from err
        focus = text.strip()
        return agent, request, {"review": True, "title": review_title(focus), **({"focus": focus} if focus else {})}

    def undo(self, sid: str, turn_seq: int | None = None) -> Event:
        """只撤销最近完整一轮的文件工具，撤销本身记在同一份追加日志里。"""
        if self.running(sid):
            raise RuntimeError_("她还在做事，先打断这一轮再撤销。")
        agent = self.agent(sid)
        session = agent.session
        with self._maintenance(sid):
            if session.branch_closed:
                raise RuntimeError_("这个会话的分支已经放弃，不能撤销。")
            workdir = session.workdir.resolve()
            expected = session.cwd
            if branch := session.branch:
                # 原 worktree 丢失或被链接替换时，不能退回原项目或别的会话的 worktree。
                tree = self.work_dir.resolve() / Path(branch["worktree"]).name
                expected = tree / branch.get("sub", "")
                if not workdir.is_relative_to(tree):
                    raise RuntimeError_("会话的临时工作目录已经不在原处，不能撤销。")
            if workdir != expected or not workdir.is_dir() or workdir != agent.tools.ws.root:
                raise RuntimeError_("会话的工作目录已经不在原处，不能撤销。")

            turn = latest_turn(session.events)
            if not turn:
                raise RuntimeError_("最近一轮没有改文件。")
            current = turn[0]["seq"]
            if turn_seq is not None and current != turn_seq:
                raise RuntimeError_("最近一轮已经变了，重新确认后再撤销。")
            if any(e["type"] == "undo" and e.get("turn_seq") == current for e in session.events):
                raise RuntimeError_("最近一轮已经撤销，没有可撤销的改动。")
            edits = turn_edits(turn)
            if not edits:
                raise RuntimeError_("最近一轮没有改文件。")
            snapshots = agent.tools.snapshots
            if snapshots is None:
                raise RuntimeError_("这个会话没有启用文件底片，不能撤销。")
            restored, skipped = snapshots.restore(agent.tools.ws, edits)
            commands = any(e["type"] == "tool" and e.get("name") == "run_command"
                           and e.get("status") == "ok" for e in turn)
            event = session.append({"type": "undo", "turn_seq": current, "restored": restored,
                                    "skipped": skipped, "commands": commands})
            self._publish(sid, event)
            return event

    # ---------- 在新分支上做 ----------

    @contextmanager
    def _maintenance(self, sid: str) -> Iterator[None]:
        """这些操作会在等待期间继续写日志，结束前不能删除会话或开始另一轮。"""
        if self.running(sid) or sid in self._operations:
            raise RuntimeError_("她还在做事，等她做完或先打断。")
        self._operations.add(sid)
        try:
            yield
        finally:
            self._operations.discard(sid)

    async def merge(self, sid: str, run_tests: bool = True) -> Event:
        """跑测试、合并回你的代码。结果记进会话（工作窗口、终端都看得到）"""
        session = self.agent(sid).session
        with self._maintenance(sid):
            info = session.branch
            if info is None:
                raise RuntimeError_("这个会话不在新分支上。")
            report = await asyncio.to_thread(merge_branch, BranchInfo.of(info), run_tests)
            event = session.append(report.as_event())
            self._publish(sid, event)
            return event

    async def discard(self, sid: str) -> Event:
        """放弃分支：删掉分支和临时目录，你的代码保持原样。这个会话之后不再接任务"""
        session = self.agent(sid).session
        with self._maintenance(sid):
            info = session.branch
            if info is None:
                raise RuntimeError_("这个会话不在新分支上。")
            await asyncio.to_thread(discard_branch, BranchInfo.of(info))
            event = session.append({"type": "branch_closed", "branch": info["branch"]})
            self._publish(sid, event)
            return event

    def _begin(self, sid: str, agent: Agent, text: str, extra: dict | None = None) -> None:
        agent.model = self.model_factory()  # 设置里换了模型或密钥：从这一轮起用新的
        events = agent.turn(text.strip(), extra)
        if extra and extra.get("review"):
            events = self._read_only(sid, agent, events)
        self._tasks[sid] = asyncio.create_task(self._drive(sid, events))

    async def _read_only(self, sid: str, agent: Agent, events: AsyncIterator[Event]) -> AsyncIterator[Event]:
        """/review 这一轮按只读模式把关；结束（做完、出错、打断）后换回原来的模式，
        中途用户换过模式就换成新的那个"""
        self._reviewing[sid] = agent.policy.mode
        agent.policy.mode = "read_only"
        try:
            async for event in events:
                yield event
        finally:
            agent.policy.mode = self._reviewing.pop(sid, agent.policy.mode)

    def project_dir(self, project_id: str) -> str:
        """代码库里登记的项目 → 它的文件夹"""
        project = self.code_projects().get(project_id) if self.code_projects else None
        if project is None:
            raise RuntimeError_("代码库里没有这个项目。")
        return project.root

    def find_project(self, name: str) -> list[tuple[str, str]]:
        """按名字找项目文件夹（代码库里登记的、做过任务的）：返回（名字，路径），名字对得上的排前面"""
        seen: dict[str, str] = {}
        if self.code_projects:
            for p in self.code_projects().all():
                seen.setdefault(str(Path(p.root).resolve()).lower(), p.root)
        for s in self.store.list(limit=100):
            seen.setdefault(str(Path(s["cwd"]).resolve()).lower(), s["cwd"])
        found = [(Path(path).name or path, path) for path in seen.values()]
        key = name.strip().lower()
        if not key:
            return found
        exact = [f for f in found if f[0].lower() == key]
        return exact or [f for f in found if key in f[0].lower() or key in f[1].lower()]

    async def compact(self, sid: str) -> None:
        agent = self.agent(sid)
        with self._maintenance(sid):
            async for event in agent.compact():
                self._publish(sid, event)

    def context(self, sid: str) -> dict:
        """上下文账本：下一步发给模型的上下文由哪几块组成、各占多少（见 ledger.py）"""
        return context_ledger(self.agent(sid))

    def interrupt(self, sid: str) -> bool:
        task = self._tasks.get(sid)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def _drive(self, sid: str, events: AsyncIterator[Event]) -> None:
        session = self.agent(sid).session
        published = len(session.events) - 1
        try:
            async for event in events:
                self._publish(sid, event)
                if "seq" in event:
                    published = event["seq"]
        except asyncio.CancelledError:
            pass  # 打断：循环已经把日志补完整了
        except Exception:
            log.exception("会话 %s 出错", sid)
        finally:
            for event in session.events[published + 1 :]:
                self._publish(sid, event)  # 打断时补上的事件
            for q in self._listeners.get(sid, set()):
                q.put_nowait(None)  # 这一轮结束了

    def _publish(self, sid: str, event: Event) -> None:
        for q in self._listeners.get(sid, set()):
            q.put_nowait(event)

    # ---------- 订阅 ----------

    def subscribe(self, sid: str, after: int = -1) -> AsyncIterator[Event]:
        """先补发 seq > after 的日志事件；正在跑的话接着推实时事件，直到这一轮结束。
        调用时就开始收实时事件（不等第一次迭代）"""
        session = self.agent(sid).session
        queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self._listeners.setdefault(sid, set()).add(queue)
        # 从这一刻起的事件都会进队列（追加和推送之间不会切到别的任务）；在这之前的从日志里补发
        attached = len(session.events) - 1
        return self._follow(sid, session, queue, after, attached)

    async def _follow(
        self, sid: str, session: Session, queue: "asyncio.Queue[Event | None]", after: int, attached: int
    ) -> AsyncIterator[Event]:
        try:
            for event in list(session.events[after + 1 : attached + 1]):
                yield event
            last = max(after, attached)
            while self.running(sid) or not queue.empty():
                event = await queue.get()
                if event is None:
                    break
                if "seq" in event:
                    if event["seq"] <= last:
                        continue
                    last = event["seq"]
                yield event
            # 刚好在补发和接实时之间结束的：日志里还有没发的，补上
            for event in list(session.events[last + 1 :]):
                yield event
        finally:
            self._listeners.get(sid, set()).discard(queue)

    async def shutdown(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
        for task in list(self._tasks.values()):
            try:
                await task
            except BaseException:
                pass
        await asyncio.gather(*(agent.tools.jobs.shutdown() for agent in self._agents.values()))


def _unfinished(session: Session) -> bool:
    """最后一句话之后没有 turn_end：这一轮没做完进程就没了"""
    last_user = max((i for i, e in enumerate(session.events) if e["type"] == "user"), default=-1)
    last_end = max((i for i, e in enumerate(session.events) if e["type"] == "turn_end"), default=-1)
    return last_user > last_end
