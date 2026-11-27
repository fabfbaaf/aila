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
from datetime import datetime
from pathlib import Path
from typing import Literal

from ..code import CodeProjects
from ..code.fix import FixError, git
from ..computer import build_computer_context, computer_shell
from ..config import ENV_FILE
from ..confirm import Confirmations
from ..llm import CoderModel
from .branch import BranchError, BranchInfo, open_branch
from .branch import discard as discard_branch
from .branch import merge as merge_branch
from .codetools import CodeAccess
from ..extensions import ExtensionStore, skills_context
from .commands import Command, load_commands, match
from .skilltool import read_skill_tool
from .delivery import DELIVERY, DeliveryError
from .external import ExternalAgent
from .localagents import ENGINES, LocalAgentError
from .ledger import context_ledger
from .mcp import TOOL_PREFIX, McpManager, bridge_tools
from .memory import ProjectMemory, project_key, remember_tool
from ..memory.episodes import coding_context, task_episode
from ..memory.store import MemoryStore
from ..tools.search import SearchProvider
from .planning import MAX_CONTINUES, PLAN_PROMPT, plan_state, plan_title, plan_tools, stage_message
from .loop import Agent, AgentConfig, Hooks
from .permissions import MODES, ComputerPolicy, Mode, Policy, Rules
from .pricing import validate_budget
from .prompt import build_context
from .review import ReviewError, review_request, review_title
from .session import Event, Session, SessionStore, latest_turn, turn_edits
from .shell import Shell, find_shell
from .snapshots import Snapshots
from .subagent import Explorer
from .webtools import WebAccess
from .workers import Workers, open_workers
from .tools import CodingTools
from .workspace import ComputerWorkspace, Workspace

log = logging.getLogger("aila.runtime")

# 编程任务里问用户的事可能要等很久（人走开了），比对话里的两分钟长得多
CONFIRM_TIMEOUT = 1800.0
BRANCH_NOTE = (
    "\n\n<分支>\n你在新分支 {branch} 上工作（git worktree 临时目录，就是上面的项目目录）。"
    "这里的改动不会直接影响用户的代码（{project}）：用户确认后才会跑测试、合并回去。"
    "不要自己合并、推送或切换分支。\n</分支>"
)
COMPUTER_PLAN_PROMPT = """先为下面这个电脑任务出一份分阶段的计划。这一轮只读，可以查看相关文件和运行查看类的命令。

目标：{goal}

1. 按目标查看电脑现状、相关文件和可用的命令或脚本接口，只查看完成目标需要的信息。
2. 用 propose_plan 交 2～8 个阶段，每阶段写清目标和可检查的验收结果。
3. 批量操作的阶段先列出每个文件的原路径、目标路径和具体改动清单，等用户看过并确认后再执行。
4. 移动、改名和其他命令副作用无法用「撤销这一轮」恢复，提前向用户说明。
5. 交完计划用一两句话说明思路和风险，等用户批准。"""


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
        mcp_config: Path | None = None,
        computer_inventory: bool = True,
        extensions: ExtensionStore | None = None,
        memory_store: "Callable[[], MemoryStore] | None" = None,
        language: Callable[[], str] = lambda: "zh",
        web_search: Callable[[], SearchProvider | None] | None = None,
    ) -> None:
        # 界面语言：英文时新会话开头说一声，让她用英文回答（定下来就不改，前缀缓存不断）
        self.language = language
        self.store = SessionStore(data_dir / "sessions")
        # 记忆库（和对话共用）：会话开头回想关于你的事和这个项目最近几次，每轮做完记一条经历；用到时现取
        self.memory_store = memory_store
        self._recorded: set[tuple[str, int]] = set()
        # 「在新分支上做」的临时目录（git worktree）
        self.work_dir = data_dir / "worktrees"
        # 代码库（代码 RAG + 调用图）：给了就让她能按意思搜、按报错定位、看调用关系
        self.code_projects = code_projects
        self.rules = Rules(data_dir / "agent_rules.json")
        self.computer_rules = Rules(data_dir / "computer_rules.json")
        self.computer_inventory = computer_inventory
        self.data_dir = data_dir
        # 项目记忆：她学到的项目约定，下次在这个项目开会话时放进环境说明
        self.memory = ProjectMemory(data_dir / "project_memory.json")
        # 项目约定也放进记忆库一份（对话里也能想起来）：改了就同步，第一次用记忆库时把已有的都放进去
        self.memory.on_change = self._sync_project_notes
        self._notes_synced = False
        # MCP 外部工具服务（和对话共用 aila-ai/mcp.json，你在设置页加）：第一次用到时在后台开起来
        # 扩展包（app/extensions.py）：命令、本事、外部工具服务
        self.extensions = extensions or ExtensionStore(data_dir / "extensions")
        self.mcp = McpManager(mcp_config or data_dir / "mcp.json", extensions=self.extensions)
        self.model_factory = model_factory
        # 「快模型」：派出去查资料的子 Agent 用（explore 工具），没给就和主模型一样
        self.fast_factory = fast_factory or model_factory
        # 联网搜索用哪家（设置里换了就用新的）；没给就是不能搜（测试里不联网）
        self.web_search: Callable[[], SearchProvider | None] = web_search or (lambda: None)
        self.confirms = confirms or Confirmations(timeout=CONFIRM_TIMEOUT)
        self.shell = shell or find_shell()
        self.config = config or AgentConfig()
        self.hooks = Hooks()
        self._agents: dict[str, Agent] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._listeners: dict[str, set[asyncio.Queue[Event | None]]] = {}
        # 看所有会话事件的（比如通知：她等你同意、做完了，推到手机），不影响任务本身
        self.watchers: list[Callable[[str, Event], None]] = []
        self._deleting: set[str] = set()
        self._operations: set[str] = set()
        # 正在 /review 的会话 → 这一轮结束后要换回的权限模式
        self._reviewing: dict[str, Mode] = {}

    # ---------- 会话 ----------

    def create(self, cwd: str | None = None, mode: Mode = "ask", isolate: bool = False,
               budget: float | None = None, *, kind: Literal["project", "computer"] = "project",
               engine: str | None = None) -> Session:
        """isolate：在新分支上做（git worktree），合并之前你的代码一个字都不动。
        engine：交给本机的 Codex / Claude Code 做（external.py）；不给就是艾拉自己做"""
        if engine is not None:
            return self._create_external(cwd, mode, isolate, budget, kind, engine)
        try:
            budget = validate_budget(budget)
        except ValueError as err:
            raise RuntimeError_(str(err)) from err
        if kind not in ("project", "computer"):
            raise RuntimeError_("不认识的任务类型。")
        if kind == "computer" and mode in ("auto", "full"):
            raise RuntimeError_("电脑任务只能选只读或先问再改。")
        if kind == "computer" and isolate:
            raise RuntimeError_("电脑任务不能在新分支上做。")
        if kind == "project" and not cwd:
            raise RuntimeError_("要告诉我在哪个项目里做。")
        root = Path.home() if kind == "computer" else Path(cwd).expanduser()  # type: ignore[arg-type]
        if not root.is_dir():
            raise RuntimeError_(f"{cwd} 不是一个目录。")
        if mode not in MODES:
            raise RuntimeError_(f"不认识的权限模式：{mode}")
        root = root.resolve()
        model = self.model_factory()
        # 启用的扩展带的本事：开会话时定下来（只放名字和简介），之后装卸扩展不改这个会话的开头，前缀缓存不断
        skills = self.extensions.skills()
        skill_note = skills_context(skills) + (ENGLISH_NOTE if self.language() == "en" else "")
        skill_meta = {"skills": [s.name for s in skills]} if skills else {}
        if kind == "computer":
            session = self.store.create(root, mode, model.name,
                                        build_computer_context(root, computer_shell().label, self.computer_inventory) + skill_note,
                                        {"kind": "computer", **skill_meta})
        elif not isolate:
            session = self.store.create(root, mode, model.name,
                                        build_context(root, self.shell.label) + self.memory.context(root)
                                        + self._recall(root) + skill_note, skill_meta)
        else:
            try:
                info = open_branch(root, self.work_dir)
            except BranchError as err:
                raise RuntimeError_(str(err)) from err
            context = (build_context(info.workdir, self.shell.label) + BRANCH_NOTE.format(branch=info.branch, project=root)
                       + self.memory.context(root) + self._recall(root) + skill_note)
            session = self.store.create(root, mode, model.name, context, {"branch": asdict(info), **skill_meta})
        if budget is not None:
            session.append({"type": "budget", "amount": budget})
        return session

    async def engine_options(self, sid: str) -> list[dict]:
        """本机 Agent 能换的设置（模型、思考深度、快速模式）：还没连上的话先连上问它"""
        agent = self._external(sid)
        try:
            return await agent.prepare()
        except LocalAgentError as err:
            raise RuntimeError_(str(err)) from err

    async def set_engine_option(self, sid: str, option_id: str, value: str) -> list[dict]:
        agent = self._external(sid)
        try:
            options = await agent.set_option(option_id, value)
        except LocalAgentError as err:
            raise RuntimeError_(str(err)) from err
        self._publish(sid, agent.session.events[-1])
        return options

    def _external(self, sid: str) -> ExternalAgent:
        agent = self.agent(sid)
        if not isinstance(agent, ExternalAgent):
            raise RuntimeError_("这个任务是艾拉自己在做：编程用的模型在 设置 → 对话 里换。")
        return agent

    def _create_external(self, cwd: str | None, mode: Mode, isolate: bool, budget: float | None, kind: str,
                         engine: str) -> Session:
        """本机 Agent 的会话：日志和艾拉自己的一样（任务页、终端、手机都能看），开头记着是谁在做"""
        if engine not in ENGINES:
            raise RuntimeError_(f"不认识的 Agent：{engine}")
        if kind != "project" or not cwd:
            raise RuntimeError_(f"{ENGINES[engine].title} 只能在项目里做：先选一个项目。")
        if mode not in MODES:
            raise RuntimeError_(f"不认识的权限模式：{mode}")
        try:
            budget = validate_budget(budget)
        except ValueError as err:
            raise RuntimeError_(str(err)) from err
        root = Path(cwd).expanduser()
        if not root.is_dir():
            raise RuntimeError_(f"{cwd} 不是一个目录。")
        root = root.resolve()
        meta: dict = {"engine": engine}
        if isolate:
            try:
                meta["branch"] = asdict(open_branch(root, self.work_dir))
            except BranchError as err:
                raise RuntimeError_(str(err)) from err
        session = self.store.create(root, mode, ENGINES[engine].title, "", meta)
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
        if engine := session.events[0].get("engine"):
            # 本机的 Codex / Claude Code 在做：权限还是按艾拉的规则（它们的批准请求交给 Policy）
            mode = session.mode if session.mode in MODES else "ask"
            external = ExternalAgent(session, str(engine), Policy(session.cwd, self.rules, mode, self.mcp),
                                     self.confirms, self.data_dir)
            if _unfinished(session):
                external.repair()
            self._agents[sid] = external  # type: ignore[assignment]
            return external  # type: ignore[return-value]
        # 在新分支上做时，她在临时目录里干活；代码库索引、记住的命令还是按你原来的项目
        computer = session.kind == "computer"
        root = session.workdir if session.workdir.is_dir() else session.cwd
        workspace = (ComputerWorkspace(root, data_dir=self.data_dir, env_file=ENV_FILE) if computer else Workspace(root))
        mode = session.mode if session.mode in MODES else "ask"
        browser = None
        # 会话开头列了扩展的本事：给她一个读全文的工具（放在工具列表最后，顺序固定）
        skill_tools = [read_skill_tool(self.extensions, session.events[0]["skills"])] if session.events[0].get("skills") else []
        if computer:
            extra = [*plan_tools(session.events), *skill_tools]
            policy = ComputerPolicy(session.cwd, self.computer_rules, mode, data_dir=self.data_dir, env_file=ENV_FILE)
        else:
            extra = CodeAccess(self.code_projects(), session.cwd).tools() if self.code_projects else []
            # 联网搜索、带着问题读网页（读网页用快模型）；派出去查资料的助手也能用
            web = WebAccess(self.fast_factory, self.web_search).tools()
            explorer = Explorer(session, workspace.root, self.shell, [*extra, *web], self.rules, self.fast_factory,
                                self.config.context_window)
            from .browsertools import BrowserAccess

            browser = BrowserAccess(workspace, session.path)
            policy = Policy(session.cwd, self.rules, mode, self.mcp)
            # 多智能体：派子 Agent 各在自己的分支里做（用编程用的模型），收回、放弃；权限跟着主会话现在的模式
            workers = Workers(session, workspace.root, self.shell, self.rules, lambda: policy.mode, self.model_factory,
                              self.work_dir, web, self.config.context_window)
            extra = [*extra, explorer.tool(), *web, *workers.tools(), *browser.tools(), *plan_tools(session.events),
                     remember_tool(self.memory, session.cwd, session.id), *bridge_tools(self.mcp), *skill_tools]
        agent = Agent(
            session,
            self.model_factory(),
            # 记忆按你原来的项目记（在新分支上做时也是）
            CodingTools(workspace, computer_shell() if computer else self.shell, extra,
                        snapshots=Snapshots(session.path.with_suffix(".snap")),
                        on_close=browser.close if browser else None),
            policy,  # type: ignore[arg-type]
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
        if agent.session.kind == "computer" and mode in ("auto", "full"):
            raise RuntimeError_("电脑任务只能选只读或先问再改。")
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
            if isinstance(agent, ExternalAgent):
                await agent.close()  # 关掉它的适配器进程
            elif agent is not None:
                await agent.tools.jobs.stop_all()
            # 还开着的子 Agent 分支（派出去没收回、也没放弃的）
            for worker in open_workers(session.events).values():
                await asyncio.to_thread(discard_branch, worker)
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

    def run(self, sid: str, text: str, review: bool = False, plan: bool = False) -> AsyncIterator[Event]:
        """开始一轮，返回这一轮的事件流（先订阅再开始，一个字都不会漏）。

        review=True：/review，只读地审一遍没提交的改动；text 是可选的「特别注意什么」。
        plan=True：先出方案，这一轮只读，text 是目标；她用 propose_plan 交计划，等你批准（decide_plan）。
        """
        agent, request, extra = self._ready(sid, text, review, plan)
        stream = self.subscribe(sid, len(agent.session.events) - 1)
        self._begin(sid, agent, request, extra)
        return stream

    def start(self, sid: str, text: str, *, plan: bool = False) -> None:
        """开始一轮，不订阅（从对话、屏幕上的报错、星图交过来的任务：工作窗口打开后自己接上）"""
        agent, request, extra = self._ready(sid, text, plan=plan)
        self._begin(sid, agent, request, extra)

    def _ready(self, sid: str, text: str, review: bool = False, plan: bool = False) -> tuple[Agent, str, dict]:
        agent = self.agent(sid)
        if self.running(sid):
            raise RuntimeError_("她还在做上一件事：等她做完，或者先打断。")
        if sid in self._operations:
            raise RuntimeError_("这个会话正在合并、放弃分支或压缩，等它结束再说。")
        if not review and not text.strip():
            raise RuntimeError_("要做什么？")
        if agent.session.branch_closed:
            raise RuntimeError_("这个会话的分支已经放弃了。开一个新任务吧。")
        if isinstance(agent, ExternalAgent) and (plan or review):
            raise RuntimeError_(f"这个任务是 {agent.engine.title} 在做：要它出方案或审查改动，直接用话跟它说。")
        if plan:
            prompt = COMPUTER_PLAN_PROMPT if agent.session.kind == "computer" else PLAN_PROMPT
            return agent, prompt.format(goal=text.strip()), {"plan_request": True, "title": plan_title(text)}
        if not review:
            # 交活：/commit、/pr、/pr-comments，请求由代码生成（改动清单、分支、PR 评论都是代码查的）
            head, _, args = text.strip().partition(" ")
            name = head[1:].lower() if head.startswith("/") else ""
            if name in DELIVERY:
                if agent.session.kind == "computer":
                    raise RuntimeError_("电脑任务不在项目里，没有可以提交或开 PR 的改动。")
                build, _label = DELIVERY[name]
                root = agent.session.workdir if agent.session.workdir.is_dir() else agent.session.cwd
                try:
                    request = build(root, args)
                except DeliveryError as err:
                    raise RuntimeError_(str(err)) from err
                return agent, request, {"command": name, "title": f"/{name}" + (f" {args.strip()}"[:200] if args.strip() else "")}
            # 自定义命令：「/发布 v1.2」展开成命令文件里写的话；日志里记着命令名和你写的那一行
            found = match(text.strip(), self.commands(agent.session.cwd, agent.session.kind))
            if found:
                cmd, args = found
                title = f"/{cmd.name}" + (f" {args}" if args else "")
                return agent, cmd.expand(args), {"command": cmd.name, "title": title[:200]}
            return agent, text, {}
        if agent.session.kind == "computer":
            raise RuntimeError_("电脑任务没有项目改动可审查。直接说要检查什么，或用 /plan 先出方案。")
        # 在新分支上做时审的是分支临时目录里的改动
        root = agent.session.workdir if agent.session.workdir.is_dir() else agent.session.cwd
        try:
            request = review_request(root, text)
        except ReviewError as err:
            raise RuntimeError_(str(err)) from err
        focus = text.strip()
        return agent, request, {"review": True, "title": review_title(focus), **({"focus": focus} if focus else {})}

    def commands(self, cwd: str | Path | None, kind: str = "project") -> dict[str, Command]:
        """这个项目能用的自定义命令（每次现读：改了命令文件马上生效）。电脑任务只用自己的"""
        project = Path(cwd) if cwd and kind != "computer" else None
        return load_commands(project, self.data_dir / "commands", self.extensions.command_dirs())

    def undo(self, sid: str, turn_seq: int | None = None) -> Event:
        """只撤销最近完整一轮的文件工具，撤销本身记在同一份追加日志里。"""
        if self.running(sid):
            raise RuntimeError_("她还在做事，先打断这一轮再撤销。")
        agent = self.agent(sid)
        if isinstance(agent, ExternalAgent):
            raise RuntimeError_(f"{agent.engine.title} 改的文件艾拉没存底片，撤销不了：用 git 看改动、还原。")
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
        if isinstance(agent, ExternalAgent):
            # 本机 Agent：它自己的模型、工具和 MCP；/review、出方案这种艾拉自己的流程不走这里（_ready 拦下了）
            self._tasks[sid] = asyncio.create_task(self._drive(sid, agent.turn(text.strip(), extra)))
            return
        agent.model = self.model_factory()  # 设置里换了模型或密钥：从这一轮起用新的
        events = agent.turn(text.strip(), extra)
        if extra and (extra.get("review") or extra.get("plan_request")):
            hint = PLAN_DENY_HINT if extra.get("plan_request") else REVIEW_DENY_HINT
            events = self._read_only(sid, agent, events, hint)
        events = self._with_mcp(agent, events)
        self._tasks[sid] = asyncio.create_task(self._drive(sid, events))

    # ---------- 长任务：按计划一阶段一阶段做 ----------

    def decide_plan(self, sid: str, approved: bool, pause_each: bool = False) -> Event:
        """你对最新那份计划的决定。批准以后用 drive_plan 开始做"""
        agent = self.agent(sid)
        if self.running(sid):
            raise RuntimeError_("她还在做事：等她做完再决定。")
        state = plan_state(agent.session.events)
        if state is None or state.decision != "proposed":
            raise RuntimeError_("现在没有等你决定的计划。")
        if approved and agent.policy.mode == "read_only":
            # 只读模式下按计划做，每个阶段都改不了文件，只会一轮轮原地打转
            raise RuntimeError_("现在是只读模式：按计划做要改文件。先换成「先问再改」再批准。")
        event = agent.session.append({"type": "plan_decision", "approved": bool(approved), "pause_each": bool(pause_each)})
        self._publish(sid, event)
        return event

    def drive_plan(self, sid: str, after: int | None = None) -> AsyncIterator[Event]:
        """按批准过的计划往下做（批准以后、停下以后接着做都用它）；返回一直到停下的事件流。
        after：从日志第几条之后开始推（批准时从批准那条开始，订阅的人也能看到这个决定）"""
        agent = self.agent(sid)
        if self.running(sid):
            raise RuntimeError_("她还在做事：等她做完，或者先打断。")
        if sid in self._operations:
            raise RuntimeError_("这个会话正在合并、放弃分支或压缩，等它结束再说。")
        if agent.session.branch_closed:
            raise RuntimeError_("这个会话的分支已经放弃了。开一个新任务吧。")
        state = plan_state(agent.session.events)
        if state is None or state.decision != "approved":
            raise RuntimeError_("没有批准过的计划。")
        if state.finished:
            raise RuntimeError_("计划已经全部做完了。")
        stream = self.subscribe(sid, len(agent.session.events) - 1 if after is None else after)
        self._tasks[sid] = asyncio.create_task(self._plan_loop(sid, agent))
        return stream

    async def _plan_loop(self, sid: str, agent: Agent) -> None:
        """一阶段一阶段地开新的一轮，直到做完或者该停下；停下的原因记成 plan_pause / plan_end 事件"""
        session = agent.session
        first = True
        try:
            while True:
                state = plan_state(session.events)
                if state is None or state.decision != "approved" or state.current is None:
                    if state is not None and state.finished:
                        self._publish(sid, session.append({"type": "plan_end"}))
                    return
                n = state.current
                stage = state.stages[n]
                last = _last_reason(session)
                if not first:
                    pause = None
                    if last not in ("done", "max_steps"):
                        pause = ("stopped", _STOP_WHY.get(last or "", "这一轮停下了"))
                    elif stage.status == "blocked":
                        pause = ("blocked", stage.summary or "她说这个阶段卡住了")
                    elif stage.status == "running" and state.continues >= MAX_CONTINUES:
                        pause = ("limit", f"第 {n + 1} 阶段接着做了 {MAX_CONTINUES} 次还没做完")
                    elif stage.status == "pending" and state.pause_each:
                        pause = ("pause_each", f"第 {n} 阶段做完了，等你看一眼再接着做")
                    if pause:
                        self._publish(sid, session.append({"type": "plan_pause", "reason": pause[0], "text": pause[1], "stage": n}))
                        return
                if stage.status == "running":
                    why = ("上一轮走到步数上限停了" if last == "max_steps"
                           else "上一轮结束时还没调用 stage_done" if last == "done" else "上次停下了")
                    text, extra = stage_message(state, n, continuing=why)
                else:
                    text, extra = stage_message(state, n)
                first = False
                agent.model = self.model_factory()
                await self._drive(sid, self._with_mcp(agent, agent.turn(text, extra)), close=False)
        finally:
            for q in self._listeners.get(sid, set()):
                q.put_nowait(None)  # 这次推进结束了

    async def _with_mcp(self, agent: Agent, events: AsyncIterator[Event]) -> AsyncIterator[Event]:
        """一轮开始前对齐 MCP 工具：等还在启动的服务一会儿，工具变了才换（一轮里不变，前缀缓存稳定）"""
        if agent.session.kind == "project":
            await self.mcp.wait_ready()
            agent.tools.replace_prefixed(TOOL_PREFIX, bridge_tools(self.mcp))
        async for event in events:
            yield event

    async def _read_only(self, sid: str, agent: Agent, events: AsyncIterator[Event], hint: str = "") -> AsyncIterator[Event]:
        """/review、出方案这一轮按只读模式把关；结束（做完、出错、打断）后换回原来的模式，
        中途用户换过模式就换成新的那个。hint：被挡下时告诉她这一轮该怎么做"""
        self._reviewing[sid] = agent.policy.mode
        agent.policy.mode = "read_only"
        agent.deny_hint = hint
        try:
            async for event in events:
                yield event
        finally:
            agent.policy.mode = self._reviewing.pop(sid, agent.policy.mode)
            agent.deny_hint = ""

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
            if s.get("kind") == "computer":
                continue
            seen.setdefault(str(Path(s["cwd"]).resolve()).lower(), s["cwd"])
        found = [(Path(path).name or path, path) for path in seen.values()]
        key = name.strip().lower()
        if not key:
            return found
        exact = [f for f in found if f[0].lower() == key]
        return exact or [f for f in found if key in f[0].lower() or key in f[1].lower()]

    async def compact(self, sid: str) -> None:
        agent = self.agent(sid)
        if isinstance(agent, ExternalAgent):
            raise RuntimeError_(f"{agent.engine.title} 自己管上下文：要压缩就跟它说 /compact。")
        with self._maintenance(sid):
            async for event in agent.compact():
                self._publish(sid, event)

    def context(self, sid: str) -> dict:
        """上下文账本：下一步发给模型的上下文由哪几块组成、各占多少（见 ledger.py）"""
        agent = self.agent(sid)
        if isinstance(agent, ExternalAgent):
            raise RuntimeError_(f"{agent.engine.title} 的上下文在它自己那边，这里看不到。")
        return context_ledger(agent)

    def interrupt(self, sid: str) -> bool:
        task = self._tasks.get(sid)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def _drive(self, sid: str, events: AsyncIterator[Event], close: bool = True) -> None:
        """跑完一轮，把事件推给订阅的人。close=False：按计划连着做，订阅留着给下一轮"""
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
            self._remember_turn(session)
            if close:
                for q in self._listeners.get(sid, set()):
                    q.put_nowait(None)  # 这一轮结束了

    # ---------- 记忆库 ----------

    def _memories(self) -> "MemoryStore | None":
        if self.memory_store is None:
            return None
        try:
            store = self.memory_store()
        except Exception as err:  # 记忆库打不开：编程照常，只是不回想、不记经历
            log.warning("记忆库打不开：%s", err)
            return None
        if not self._notes_synced:
            self._notes_synced = True
            try:
                for key, notes in self.memory.all_projects():
                    store.sync_project(key, [(n.id, n.text) for n in notes])
            except Exception as err:
                log.warning("项目记忆放进记忆库失败：%s", err)
        return store

    def _recall(self, root: Path) -> str:
        """会话开头的「回想」：关于你的几条 + 这个项目最近几次做了什么（定下来就不改）"""
        try:
            return coding_context(self._memories(), project_key(root), datetime.now().astimezone())
        except Exception as err:
            log.warning("回想失败：%s", err)
            return ""

    def _remember_turn(self, session: Session) -> None:
        """一轮做完（做完、出错、打断都算），记一条经历：让她做什么、改了哪些文件、最后怎么说的"""
        if session.kind != "project" or not session.events or session.events[-1].get("type") != "turn_end":
            return
        key = (session.id, len(session.events))
        if key in self._recorded:
            return
        self._recorded.add(key)
        try:
            text = task_episode(session.events)
            store = self._memories() if text else None
            if store is not None:
                store.add_episode(project_key(session.cwd), text, {"source": "task", "session": session.id})
        except Exception as err:
            log.warning("记经历失败：%s", err)

    def _sync_project_notes(self, key: str, notes: list) -> None:
        store = self._memories()
        if store is not None:
            store.sync_project(key, [(n.id, n.text) for n in notes])

    def _publish(self, sid: str, event: Event) -> None:
        for q in self._listeners.get(sid, set()):
            q.put_nowait(event)
        for watch in self.watchers:
            try:
                watch(sid, event)
            except Exception:  # 看的人出错不能拖垮任务
                log.exception("事件观察者出错")

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
        await asyncio.gather(*(agent.close() if isinstance(agent, ExternalAgent) else agent.tools.jobs.shutdown()
                               for agent in self._agents.values()))
        await self.mcp.stop_all()


PLAN_DENY_HINT = ("这一轮只出方案、不动手：不要换别的办法去改，把要改的地方写进计划，用 propose_plan 交上来，"
                  "用户批准以后才会一阶段一阶段去做。")
REVIEW_DENY_HINT = "这一轮只是审查改动、不动手：把发现的问题和建议写下来就好。"

ENGLISH_NOTE = "\n\nThe user chose the English interface: reply and explain in English (keep code comments in the project's existing language)."

_STOP_WHY = {
    "interrupted": "你打断了",
    "budget": "花到上限了",
    "error": "出错了",
    "rejected": "你没同意她要做的操作",
    "timeout": "等你确认等超时了",
    "denied": "她要做的事连着被权限挡下（比如只读模式下要改文件）",
}


def _last_reason(session: Session) -> str | None:
    """最近一轮怎么结束的（turn_end 的 reason）"""
    return next((e.get("reason") for e in reversed(session.events) if e["type"] == "turn_end"), None)


def _unfinished(session: Session) -> bool:
    """最后一句话之后没有 turn_end：这一轮没做完进程就没了"""
    last_user = max((i for i, e in enumerate(session.events) if e["type"] == "user"), default=-1)
    last_end = max((i for i, e in enumerate(session.events) if e["type"] == "turn_end"), default=-1)
    return last_user > last_end
