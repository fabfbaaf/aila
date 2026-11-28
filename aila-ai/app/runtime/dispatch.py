"""跨 Agent 派工：艾拉或本机 Agent 可以拟定分工，让选定的协作者在隔离的工作区开工。

用户说「前端你做，后端交给 Codex」，主 Agent 用艾拉给它的工具派出去：
- 艾拉给主 Agent 挂一个本机的 MCP 服务（POST /agent/bridge，带这个会话专用的令牌），里面是派工的几个工具
- 派出去的是一个普通的编程会话：开在独立的 git 工作区（新分支，branch.py），用户的代码在合并前一个字都不动；
  会话开头记着是谁派的（parent），任务页左边列表里也看得到，能点进去看
- 助手要你同意的操作（确认卡片）原样转到主任务里：在主任务里就能点；它做完、出错也在主任务里说一声
- 收回：主 Agent 叫 merge_work。合并 = 写进用户的代码，所以按主任务的权限模式问你（给你看的 diff 由这里生成）；
  合并前跑项目自带的测试，有冲突不合并（主 Agent 同时在原地改了同一个文件就算冲突）。问你的时候不卡住主 Agent：
  先告诉它「在等用户同意」，同意以后在后台合并，结果用 work_status 看
- 工具调用有超时（Codex 默认一分钟），所以「等它做完」一次最多等 50 秒，没做完就说还在做
- 交回：它一轮结束（做完、出错、卡住），主任务闲着就开一轮，把代码整理的交回报告交给主模型汇总（handback.py）；
  主任务正在做就等它这一轮结束再叫，这一轮里已经用 work_status / wait_work 看过的不再叫。你打断的不叫
- 服务重启：派出去的活做到一半进程没了，打开主任务时说一声是哪几个，可以去它的任务里接着说或者重新派

为什么是 MCP 而不是让主 Agent 自己跑命令：派活、开工作区、合并都由艾拉的代码做，权限、日志、界面都是现成的；
主 Agent 只是说「派谁、做什么」。
"""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
import secrets
import time
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..code.fix import FixError, repo_root
from . import acceptance
from .branch import BranchError, BranchInfo, MergeReport, changes, discard, open_branch
from .collaboration import CollaborationError, PairPlan, apply_candidate, check_candidate, prepare_candidate, tree
from .handback import Handback, report_text, report_title, should_wake
from .localagents import ENGINES
from .tools import CodingTool, Outcome

if TYPE_CHECKING:
    from .runner import Runtime
    from .session import Event, Session

log = logging.getLogger("aila.dispatch")

SERVER_NAME = "aila"
WAIT_MAX = 50  # 秒：一次最多等这么久（主 Agent 的工具调用超时一般是 60 秒）
MAX_DIFF = 60_000
MAX_SAID = 1_500

PAIR_TOOLS: list[dict[str, Any]] = [
    {
        "name": "delegate_pair",
        "description": (
            "前后端等两项需要一起交付的改动用这个工具：确认分工、接口、验证场景后一起派给两个本机 Agent。"
            "双方按同一份当前代码快照和接口约定工作。都做完后用 validate_pair 联合验收并统一收回。"
        ),
        "inputSchema": {"type": "object", "properties": {
            "contract": {"type": "string", "description": "双方共享的请求、响应、错误格式等接口约定"},
            "acceptance": {"type": "string", "description": "需要实际验证的用户操作与预期结果"},
            "test_file": {"type": "string", "description": "覆盖联合验收场景的现有或计划新增的测试文件（相对仓库根目录，pytest 或 npm test）"},
            "assignments": {"type": "array", "minItems": 2, "maxItems": 2, "items": {
                "type": "object", "properties": {
                    "agent": {"type": "string", "enum": list(ENGINES)},
                    "model": {"type": "string", "description": "用户指定的模型编号。留空用这个 Agent 的默认模型"},
                    "title": {"type": "string"},
                    "task": {"type": "string"},
                    "paths": {"type": "array", "items": {"type": "string"},
                              "description": "这项工作负责的仓库相对文件或目录，两边不能重叠"},
                }, "required": ["agent", "title", "task", "paths"],
            }},
        }, "required": ["contract", "acceptance", "test_file", "assignments"]},
    },
    {
        "name": "validate_pair",
        "description": "协作者都完成后，在临时工作区把改动放到一起，跑指定的验收测试和相关项目测试。通过后在主任务页统一确认一次。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "pair_status",
        "description": "查看联合验收的进度、各子任务状态、验收结果或要处理的冲突。失败后可以修完再验收。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "discard_pair",
        "description": "结束这组派工，清理临时验收工作区。子任务工作区留在任务列表，便于查看或继续修。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

TOOLS: list[dict[str, Any]] = [
    {
        "name": "delegate",
        "description": (
            "把一块活派给这台电脑上的另一个编程 Agent（codex 或 claude）去做。它在一个独立的 git 工作区（新分支）里改，"
            "用户的代码在合并前不动；它要用户同意的操作会出现在用户的任务页里。派出去以后用 wait_work 等它做完，"
            "看结果没问题再用 merge_work 合并回来（会先跑项目的测试）。适合「前端你做，后端交给 Codex」这种分工：两边改的文件不要重叠。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "enum": list(ENGINES), "description": "派给哪一个已安装并可用的本机 Agent"},
                "title": {"type": "string", "description": "这块活的短名字，比如「后端接口」"},
                "task": {"type": "string", "description": "完整的任务说明：要做什么、改哪些地方、怎么验证。它看不到你们之前的对话"},
            },
            "required": ["agent", "title", "task"],
        },
    },
    {
        "name": "work_status",
        "description": "看派出去的活现在怎么样：在做、在等用户同意、做完了还是出错了；它最后说了什么、改了哪些文件、合并了没有。",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string", "description": "delegate 返回的编号"}},
                        "required": ["id"]},
    },
    {
        "name": "wait_work",
        "description": f"等派出去的活做完（一次最多等 {WAIT_MAX} 秒，没做完就返回现在的情况，可以再等）。",
        "inputSchema": {"type": "object", "properties": {
            "id": {"type": "string", "description": "delegate 返回的编号"},
            "seconds": {"type": "integer", "description": f"最多等几秒（1～{WAIT_MAX}）"},
        }, "required": ["id"]},
    },
    {
        "name": "merge_work",
        "description": "把派出去的活做好的改动合并回用户的代码：先跑项目的测试，全过、没有冲突才合并；按用户的权限模式，可能要等用户同意。",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string", "description": "delegate 返回的编号"}},
                        "required": ["id"]},
    },
    {
        "name": "discard_work",
        "description": "不要派出去的这块活了：删掉它的分支和工作区，用户的代码保持原样。",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string", "description": "delegate 返回的编号"}},
                        "required": ["id"]},
    },
]
TOOLS += PAIR_TOOLS


class DispatchError(Exception):
    """说给主 Agent 听的（它会转告用户）"""


class Dispatch:
    def __init__(self, runtime: "Runtime", base_url: str | None) -> None:
        self.rt = runtime
        self.base_url = base_url  # 艾拉自己的地址（http://127.0.0.1:8765）；没有就不给主 Agent 派工工具
        self._tokens: dict[str, str] = {}  # 令牌 → 主会话
        self._merging: dict[str, str] = {}  # 正在等同意或正在合并的助手会话 → 现在的情况
        self._pair_jobs: dict[str, asyncio.Task[None]] = {}
        self._pair_committing: set[str] = set()
        # 交回：主任务 → 结束了、还没交给主模型看的子任务（按结束先后）
        self._unreported: dict[str, dict[str, None]] = {}
        self._wakers: set[asyncio.Task[None]] = set()
        runtime.watchers.append(self._mirror)

    # ---------- 给主 Agent 挂的 MCP 服务 ----------

    def servers(self, parent_sid: str) -> list[dict[str, Any]]:
        """开会话、加载会话时交给本机 Agent 的 MCP 服务（ACP 的 mcp_servers，http 类型）"""
        if not self.base_url:
            return []
        token = next((t for t, sid in self._tokens.items() if sid == parent_sid), None) or secrets.token_urlsafe(32)
        self._tokens[token] = parent_sid
        return [{"type": "http", "name": SERVER_NAME, "url": f"{self.base_url}/agent/bridge",
                 "headers": [{"name": "Authorization", "value": f"Bearer {token}"}]}]

    def session_for(self, token: str) -> str | None:
        return self._tokens.get(token)

    def tools_for(self, sid: str) -> list[dict[str, Any]]:
        """给已选定协作者的会话精确的工具参数，模型只填写分工内容。"""
        parent = self.rt.store.get(sid)
        collaborators = parent.events[0].get("collaborators", []) if parent is not None else []
        if not collaborators:
            return TOOLS
        propose = deepcopy(PAIR_TOOLS[0])
        propose["description"] = (
            "按用户预选的协作者顺序安排工作。每人一项不重叠的改动，写清共同约定和验收测试。"
            "提交后由用户确认并派工。完成后用 validate_pair 联合验收。"
        )
        assignments = propose["inputSchema"]["properties"]["assignments"]
        assignments.update(minItems=len(collaborators), maxItems=len(collaborators))
        fields = assignments["items"]
        for key in ("agent", "model"):
            del fields["properties"][key]
        fields["required"].remove("agent")
        return [propose, PAIR_TOOLS[1], PAIR_TOOLS[2], PAIR_TOOLS[3]]

    def planning_tools(self, sid: str) -> list[CodingTool]:
        """让艾拉用自己的项目记忆和检索工具拟分工，实际开工由用户确认。"""
        def build(spec: dict[str, Any], kind: str, label: str) -> CodingTool:
            async def run(args: dict[str, Any]) -> Outcome:
                try:
                    return Outcome(await self.call(sid, spec["name"], args))
                except DispatchError as err:
                    return Outcome(str(err), ok=False)

            return CodingTool(spec["name"], spec["description"], spec["inputSchema"], run, lambda _: kind, lambda _: label)

        definitions = self.tools_for(sid)
        specs = {tool["name"]: tool for tool in definitions}
        preset = definitions is not TOOLS
        tools = [build(specs["delegate_pair"], "meta" if preset else "delegate", "准备分工"),
                 build(specs["pair_status"], "meta", "查看协作")]
        if preset:
            tools.append(build(specs["validate_pair"], "delegate", "联合验收"))
        return tools

    async def call(self, parent_sid: str, name: str, args: dict[str, Any]) -> str:
        parent = self.rt.store.get(parent_sid)
        if parent is not None and parent.events[0].get("pair"):
            raise DispatchError("这项工作的派工和收回由主任务负责。")
        if name in ("delegate_pair", "validate_pair", "pair_status", "discard_pair"):
            if parent is None:
                raise DispatchError("没有这个主任务。")
            try:
                if name == "delegate_pair":
                    return self.propose_pair(parent, args)
                if name == "validate_pair":
                    return self.validate_pair(parent)
                if name == "discard_pair":
                    return await self.discard_pair(parent)
                return self.pair_status(parent)
            except (CollaborationError, BranchError, FixError, OSError) as err:
                raise DispatchError(str(err)) from err
        if name == "delegate":
            if parent is not None and parent.events[0].get("collaborators"):
                raise DispatchError("这个任务已经选好了协作者，用 delegate_pair 提交分工。")
            return await self.delegate(parent_sid, str(args.get("agent", "")), str(args.get("title", "")), str(args.get("task", "")))
        child = self._child(parent_sid, str(args.get("id", "")))
        if not self.rt.running(child.id):
            self._seen(parent_sid, child.id)  # 主模型自己看过、在收了：结束时不用再叫它
        if name == "work_status":
            return self.status(child)
        if name == "wait_work":
            seconds = max(1, min(WAIT_MAX, int(args.get("seconds") or WAIT_MAX)))
            deadline = time.monotonic() + seconds
            while self.rt.running(child.id) and time.monotonic() < deadline and not child.waiting():
                await asyncio.sleep(0.5)
            if not self.rt.running(child.id):
                self._seen(parent_sid, child.id)
            return self.status(child)
        if name == "merge_work":
            if child.events[0].get("pair"):
                raise DispatchError("这项工作要由主任务一起收回，先用 validate_pair 联合验收。")
            return await self.merge(parent_sid, child)
        if name == "discard_work":
            if self.rt.running(child.id):
                raise DispatchError("它还在做，先等它做完（或者让用户在任务页里打断）再放弃。")
            await self.rt.discard(child.id)
            self._note(parent_sid, f"不要「{self._title(child)}」了：分支删了，代码保持原样")
            return "放弃了：分支和工作区都删了，用户的代码保持原样。"
        raise DispatchError(f"没有叫 {name} 的工具。")

    # ---------- 成组派工与联合验收 ----------

    @staticmethod
    def _pair(parent: "Session") -> dict[str, Any] | None:
        for event in reversed(parent.events):
            if event["type"] in ("pair_applied", "pair_closed"):
                return None
            if event["type"] == "pair_plan":
                return event
        return None

    @staticmethod
    def _pair_event(parent: "Session", pair: dict[str, Any], kind: str) -> dict[str, Any] | None:
        return next((e for e in reversed(parent.events) if e["type"] == kind and e.get("pair_id") == pair["pair_id"]), None)

    @staticmethod
    def proposal(parent: "Session") -> dict[str, Any] | None:
        for event in reversed(parent.events):
            if event["type"] in ("pair_proposal_rejected", "pair_plan"):
                return None
            if event["type"] == "pair_proposal":
                return event
        return None

    def opening_prompt(self, parent: "Session") -> str:
        """第一次派工前，每轮都提醒主模型用预选名单拟定分工。"""
        picks = parent.events[0].get("collaborators", [])
        if not picks or any(e["type"] == "pair_plan" for e in parent.events):
            return ""
        if self.proposal(parent):
            return "分工已提交，等用户在协作卡片里确认或退回。这一轮可以解释方案，先别改文件。"
        roster = "\n".join(f"{index}. {ENGINES[pick['agent']].title} · {pick['model'] or '默认模型'}"
                           for index, pick in enumerate(picks, 1))
        return (
            "你是本次任务的主 Agent。调用艾拉 MCP 的 delegate_pair 工具提交分工，等用户确认。"
            "如果目标已经写清文件范围和验收条件，直接提交；缺少信息时再只读查看仓库。"
            "如果工具需要先搜索，用完整名称 mcp__aila__delegate_pair 查找。先拟分工，别自己实现。"
            "读取工具失败时，用已有信息拟分工；信息不够就问用户。"
            "按以下顺序给每位协作者分配一项有独立负责范围的工作，写清共同约定与验收测试。"
            "如果目标不清楚或拆不出足够的工作，说明原因并停下。\n" + roster
        )

    def propose_pair(self, parent: "Session", args: dict[str, Any]) -> str:
        if parent.mode == "read_only" or parent.branch_closed or parent.events[0].get("pair"):
            raise CollaborationError("要在可编辑的主任务里发起成组派工。")
        if self._pair(parent) or self.proposal(parent):
            raise CollaborationError("已有待确认或正在进行的成组派工。")
        collaborators = parent.events[0].get("collaborators", [])
        assignments = args.get("assignments")
        if (collaborators and isinstance(assignments, list) and len(assignments) == len(collaborators)
                and all(isinstance(item, dict) for item in assignments)):
            args = {**args, "assignments": [{**item, **pick} for item, pick in zip(assignments, collaborators, strict=True)]}
        plan = PairPlan.parse(args, set(ENGINES), len(collaborators) or 2)
        self._append(parent.id, {"type": "pair_proposal", "proposal_id": secrets.token_urlsafe(12),
                                 "preset": bool(collaborators), **asdict(plan)})
        return "分工已放到主任务的「协作」里，等用户确认后才开工。"

    async def decide_pair(self, parent: "Session", proposal_id: str, approved: bool,
                          picks: list[dict[str, str]] | None = None) -> str:
        proposal = self.proposal(parent)
        if proposal is None or proposal["proposal_id"] != proposal_id:
            raise CollaborationError("这份分工已经处理，刷新任务后再试。")
        if not approved:
            self._append(parent.id, {"type": "pair_proposal_rejected", "proposal_id": proposal_id})
            return "已退回分工。"
        assignments = [dict(a, paths=list(a["paths"])) for a in proposal["assignments"]]
        if picks is not None:
            if len(picks) != len(assignments):
                raise CollaborationError("每项工作都要选执行者。")
            if proposal.get("preset") and any(
                pick != {"agent": item["agent"], "model": item["model"]}
                for item, pick in zip(assignments, picks, strict=True)
            ):
                raise CollaborationError("这份分工要使用开任务时选好的协作者。")
            for assignment, pick in zip(assignments, picks, strict=True):
                assignment.update(pick)
        return await self.delegate_pair(parent, {**proposal, "assignments": assignments})

    async def delegate_pair(self, parent: "Session", args: dict[str, Any]) -> str:
        if parent.mode == "read_only" or parent.branch_closed:
            raise CollaborationError("主任务需要允许改文件，并且工作区要还在。")
        if self._pair(parent):
            raise CollaborationError("这组工作还在任务里，先用 pair_status 查看进度。")
        plan = PairPlan.parse(args, set(ENGINES), len(parent.events[0].get("collaborators", [])) or 2)
        target = parent.workdir
        snapshot = open_branch(target, self.rt.work_dir)
        members = []
        unclaimed: list[BranchInfo] = []
        try:
            for assignment in plan.assignments:
                unclaimed.append(open_branch(target, self.rt.work_dir, base=snapshot.base))
            for assignment in plan.assignments:
                info = unclaimed[0]
                child = self.rt.create(str(parent.cwd), parent.mode, isolate=True, engine=assignment.agent,
                                       parent=parent.id, pair_branch=info)  # type: ignore[arg-type]
                unclaimed.pop(0)
                members.append({"id": child.id, **asdict(assignment)})
                self.rt.rename(child.id, f"{assignment.title}（{ENGINES[assignment.agent].title}）")
                if assignment.model:
                    child.append({"type": "engine_initial_model", "value": assignment.model})
                elif not parent.events[0].get("collaborators") and parent.events[0].get("engine") == assignment.agent:
                    for e in parent.events:
                        if e["type"] == "engine_config":
                            child.append({"type": "engine_config", "id": e["id"], "value": e["value"]})
        except Exception:
            for member in members:
                await self.rt.discard(member["id"])
            raise
        finally:
            for info in unclaimed:
                discard(info)
            discard(snapshot)

        self._append(parent.id, {"type": "pair_plan", "pair_id": snapshot.branch, "base": snapshot.base,
                                 "target": str(target.resolve()), "repo": snapshot.repo, "members": members,
                                 "contract": plan.contract, "acceptance": plan.acceptance, "test_file": plan.test_file})
        for member in members:
            self.rt.start(member["id"], (
                f"{member['task']}\n\n共同的接口约定\n{plan.contract}\n\n"
                f"你负责的路径\n{', '.join(member['paths'])}\n\n"
                f"联合验收场景\n{plan.acceptance}\n验收测试文件：{plan.test_file}\n"
                "在自己的工作区里完成这部分。结束时说清做完的内容、跑过的测试和卡点。"
            ))
        self._note(parent.id, f"已按同一份代码派工：{'、'.join(m['title'] for m in members)}。")
        return json.dumps({"children": [m["id"] for m in members], "base": snapshot.base,
                           "next": "协作者都结束后，用 validate_pair 联合验收。"}, ensure_ascii=False)

    def pair_status(self, parent: "Session") -> str:
        pair = self._pair(parent)
        if pair is None:
            last = next((e for e in reversed(parent.events) if e["type"] in ("pair_applied", "pair_closed")), None)
            return last.get("text", "") if last else "还没有成组派工。"
        stage = self._pair_event(parent, pair, "pair_stage")
        result = self._pair_event(parent, pair, "pair_result")
        details = [f"联合验收要求：{pair['acceptance']}", f"测试：{pair['test_file']}"]
        for member in pair["members"]:
            child = self.rt.store.get(member["id"])
            details.append(f"{member['title']}：" + (self.status(child) if child else "任务已删除"))
        if parent.id in self._pair_jobs:
            details.append("联合验收正在跑，或在等主任务的合并确认。")
        if result:
            details.append(result["text"])
        if stage and Path(stage["branch"]["worktree"]).is_dir():
            details.append(f"这次验收的临时工作区：{stage['branch']['worktree']}")
        return "\n".join(details)

    def pair_active(self, sid: str) -> bool:
        return sid in self._pair_jobs

    def active_pair(self, parent: "Session") -> bool:
        return self._pair(parent) is not None

    def _members(self, parent: "Session", pair: dict[str, Any]) -> list[tuple[BranchInfo, list[str], str]]:
        infos = []
        for member in pair["members"]:
            child = self._child(parent.id, member["id"])
            final = next((e for e in reversed(child.events) if e["type"] == "turn_end"), None)
            if self.rt.running(child.id) or not final or final.get("reason") != "done" or not child.branch:
                raise CollaborationError(f"「{member['title']}」还没正常交回，先继续或处理它的卡点。")
            infos.append((BranchInfo.of(child.branch), member["paths"], member["title"]))
        return infos

    def validate_pair(self, parent: "Session") -> str:
        pair = self._pair(parent)
        if not pair:
            raise CollaborationError("先用 delegate_pair 提交分工并派出去。")
        if parent.id in self._pair_jobs:
            return "联合验收已经开始，用 pair_status 查看。"
        if parent.mode == "read_only" or parent.branch_closed:
            raise CollaborationError("主任务需要允许改文件，并且工作区要还在。")
        members = self._members(parent, pair)
        self._pair_jobs[parent.id] = asyncio.create_task(self._validate_pair(parent, pair, members))
        return "正在临时工作区做联合验收。测试通过后会在主任务里确认，进度用 pair_status 看。"

    async def _clean_pair_stage(self, parent: "Session", pair: dict[str, Any]) -> None:
        event = self._pair_event(parent, pair, "pair_stage")
        if event:
            info = BranchInfo.of(event["branch"])
            path = Path(info.worktree).resolve()
            if (path.is_dir() and path.parent == self.rt.work_dir.resolve()
                    and info.branch == f"aila/{path.name}" and info.repo == pair["repo"]):
                await asyncio.to_thread(discard, info)

    async def discard_pair(self, parent: "Session") -> str:
        pair = self._pair(parent)
        if not pair or parent.id in self._pair_jobs:
            raise CollaborationError("没有可结束的派工，或还在验收、等确认。")
        cleanup = asyncio.create_task(self._clean_pair_stage(parent, pair))
        self._pair_jobs[parent.id] = cleanup
        try:
            await cleanup
        finally:
            self._pair_jobs.pop(parent.id, None)
            self.rt.close_idle_streams(parent.id)
        text = "这组派工已结束，子任务工作区留在任务列表。"
        self._append(parent.id, {"type": "pair_closed", "pair_id": pair["pair_id"], "text": text})
        return text

    async def _validate_pair(self, parent: "Session", pair: dict[str, Any],
                             members: list[tuple[BranchInfo, list[str], str]]) -> None:
        report = MergeReport(False, "")
        stage: BranchInfo | None = None
        start = time.monotonic()
        try:
            target = parent.workdir.resolve()
            if str(target) != pair["target"] or str(repo_root(target)) != pair["repo"]:
                raise CollaborationError("主任务的工作区变了，重新派工。")
            await self._clean_pair_stage(parent, pair)
            stage = await asyncio.to_thread(open_branch, target, self.rt.work_dir)
            self._append(parent.id, {"type": "pair_stage", "pair_id": pair["pair_id"], "branch": asdict(stage)})
            signatures = [await asyncio.to_thread(tree, info) for info, _, _ in members]
            report.files = await asyncio.to_thread(prepare_candidate, stage, pair["base"], members)
            expected, report.tests = await asyncio.to_thread(check_candidate, stage, report.files, pair["test_file"])
            failed = next((t for t in report.tests if not t.ok), None)
            if failed:
                raise CollaborationError(f"{failed.label} 没通过。由主任务根据测试输出安排修复。")
            if parent.mode == "read_only" or parent.branch_closed:
                raise CollaborationError("主任务现在不能收回改动。")
            diff = await asyncio.to_thread(_diff, stage, await asyncio.to_thread(changes, stage))
            self._append(parent.id, {"type": "pair_review", "pair_id": pair["pair_id"],
                                     "files": report.files, "tests": report.as_event()["tests"], "detail": diff})
            self._note(parent.id, f"联合验收通过（{len(report.tests)} 项），现在等你确认收回协作者的改动。")
            pending = self.rt.confirms.open()
            try:
                self._append(parent.id, {"type": "confirm", "id": pending.id, "call_id": f"pair-{pending.id}",
                                         "tool": "edit_file", "kind": "edit", "detail": diff, "reason": "",
                                         "action": f"收回通过联合验收的协作改动（{len(report.files)} 个文件）",
                                         "can_remember": False})
                result = await self.rt.confirms.wait(pending)
            finally:
                self.rt.confirms.close(pending)
            self._append(parent.id, {"type": "confirm_end", "id": pending.id, "result": result, "remember": False})
            if result != "approved":
                raise CollaborationError("这次没有收到收回改动的确认，验收工作区还在。")
            if parent.mode == "read_only" or parent.branch_closed or target != parent.workdir.resolve():
                raise CollaborationError("主任务确认后换了工作区或权限，重新验收。")
            self._members(parent, pair)
            if signatures != [await asyncio.to_thread(tree, info) for info, _, _ in members]:
                raise CollaborationError("有子任务在测试后又改了代码，重新验收。")
            if parent.mode == "read_only":
                raise CollaborationError("主任务现在不能收回改动。")
            self._pair_committing.add(parent.id)
            await asyncio.to_thread(apply_candidate, stage, expected, self.rt.work_dir)
            report.merged = True
            report.reason = "联合验收通过，协作改动已收回到主任务工作区。"
            self._append(parent.id, {"type": "pair_applied", "pair_id": pair["pair_id"], "text": report.reason})
        except (CollaborationError, DispatchError, BranchError, FixError, OSError) as err:
            report.reason = str(err)
            if stage is not None:
                report.reason += f" 验收工作区：{stage.worktree}"
            self._append(parent.id, {"type": "pair_result", "pair_id": pair["pair_id"], "text": report.reason})
        finally:
            try:
                if report.reason:
                    report.seconds = round(time.monotonic() - start, 1)
                    self._append(parent.id, report.as_event())
                if report.merged and stage is not None:
                    await asyncio.to_thread(discard, stage)
            finally:
                self._pair_committing.discard(parent.id)
                self._pair_jobs.pop(parent.id, None)
                self.rt.close_idle_streams(parent.id)

    async def shutdown(self) -> None:
        for waker in list(self._wakers):
            waker.cancel()
        jobs = list(self._pair_jobs.items())
        for sid, job in jobs:
            if sid not in self._pair_committing:
                job.cancel()
        await asyncio.gather(*(job for _, job in jobs), return_exceptions=True)

    # ---------- 派活 ----------

    async def delegate(self, parent_sid: str, agent: str, title: str, task: str) -> str:
        if agent not in ENGINES:
            raise DispatchError(f"只能派给 {' 或 '.join(ENGINES)}。")
        title, task = title.strip()[:40] or "派出去的活", task.strip()
        if not task:
            raise DispatchError("要写清楚让它做什么。")
        parent = self.rt.agent(parent_sid).session
        mode = parent.mode
        try:
            child = self.rt.create(str(parent.cwd), mode, isolate=True, engine=agent, parent=parent_sid)  # type: ignore[arg-type]
        except Exception as err:  # 不是 git 仓库、开分支失败
            raise DispatchError(f"派不出去：{err}") from err
        self.rt.rename(child.id, f"{title}（{ENGINES[agent].title}）")
        if parent.events[0].get("engine") == agent:
            # 派给同一种 Agent：沿用主任务里选的模型、思考深度这些（不然用它的默认，可能是最贵的那个）
            for e in parent.events:
                if e["type"] == "engine_config":
                    child.append({"type": "engine_config", "id": e["id"], "value": e["value"]})
        self.rt.start(child.id, task)
        branch = (child.branch or {}).get("branch", "")
        self._append(parent_sid, {"type": "note", "text": f"派给 {ENGINES[agent].title}：「{title}」，在分支 {branch} 的独立工作区里做",
                                  "dispatched": child.id})
        return json.dumps({
            "id": child.id, "agent": agent, "branch": branch,
            "next": "用 wait_work 等它做完；它要用户同意的操作会出现在用户的任务页里。做完用 merge_work 合并回来。",
        }, ensure_ascii=False)

    def _child(self, parent_sid: str, child_sid: str) -> "Session":
        session = self.rt.store.get(child_sid)
        if session is None or session.events[0].get("parent") != parent_sid:
            raise DispatchError("没有这个编号的活（只能管这个任务派出去的）。")
        return session

    def _title(self, child: "Session") -> str:
        return child.title.split("（")[0] if child.title else "派出去的活"

    # ---------- 看情况 ----------

    def status(self, child: "Session") -> str:
        running = self.rt.running(child.id)
        end = next((e for e in reversed(child.events) if e["type"] == "turn_end"), None)
        pending = child.pending_confirm()
        if child.branch_closed:
            state = "已经放弃了"
        elif child.id in self._merging:
            state = self._merging[child.id]
        elif pending:
            state = f"在等用户在任务页里同意：{pending.get('action', '')}"
        elif running:
            state = "还在做"
        elif end is None:
            state = "还没开始"
        else:
            state = {"done": "做完了", "error": f"出错了：{end.get('error', '')}", "interrupted": "被用户打断了",
                     "rejected": "用户没同意它要做的操作，停下了", "denied": "被权限挡下了，停下了",
                     "max_steps": "步数到上限了，停下了"}.get(str(end.get("reason")), str(end.get("reason")))
        said = next((e.get("text", "") for e in reversed(child.events) if e["type"] == "assistant" and e.get("text")), "")
        merged = [e for e in child.events if e["type"] == "merge"]
        lines = [f"编号：{child.id}", f"情况：{state}"]
        if said:
            lines.append(f"它最后说：{said[-MAX_SAID:]}")
        try:
            files = changes(BranchInfo.of(child.branch)) if child.branch and not child.branch_closed else []
        except Exception:  # 工作区没了
            files = []
        if files:
            lines.append("分支上还没合并的改动：" + "、".join(f"{s} {p}" for s, p in files[:40]))
        if merged:
            last = merged[-1]
            tests = "；".join(f"{'✓' if t.get('ok') else '✗'} {t.get('label')}" for t in last.get("tests", []))
            lines.append(f"上次合并：{'合并了' if last.get('merged') else '没合并'}，{last.get('reason', '')}" + (f"（测试：{tests}）" if tests else ""))
        return "\n".join(lines)

    # ---------- 收回 ----------

    async def merge(self, parent_sid: str, child: "Session") -> str:
        if self.rt.running(child.id):
            raise DispatchError("它还在做，等它做完再合并（wait_work）。")
        if child.id in self._merging:
            return self.status(child)
        if not child.branch or child.branch_closed:
            raise DispatchError("这块活的分支已经没了。")
        info = BranchInfo.of(child.branch)
        files = await asyncio.to_thread(changes, info)
        if not files:
            return "分支上没有要合并的改动。"
        parent_agent = self.rt.agent(parent_sid)
        decision = parent_agent.policy.decide("edit")
        if decision.verdict == "deny":
            raise DispatchError(decision.reason or "现在不能改用户的代码。")
        title = self._title(child)
        if decision.verdict == "allow":
            return await self._merge_now(parent_sid, child, title)
        # 要问用户：不卡住主 Agent，问完在后台合并
        self._merging[child.id] = "在等用户在任务页里同意合并"
        diff = await asyncio.to_thread(_diff, info, files)
        asyncio.get_running_loop().create_task(self._ask_then_merge(parent_sid, child, title, diff, len(files)))
        return f"已经请用户在任务页里确认合并「{title}」（{len(files)} 个文件）。过一会儿用 work_status 看结果。"

    async def _ask_then_merge(self, parent_sid: str, child: "Session", title: str, diff: str, count: int) -> None:
        confirms = self.rt.confirms
        pending = confirms.open()
        call_id = f"merge-{child.id}"
        try:
            self._append(parent_sid, {"type": "confirm", "id": pending.id, "call_id": call_id, "tool": "edit_file", "kind": "edit",
                                      "action": f"把「{title}」的改动合并到你的代码（{count} 个文件，先跑测试）",
                                      "detail": diff, "reason": "", "can_remember": False})
            result = await confirms.wait(pending)
        finally:
            confirms.close(pending)
        self._append(parent_sid, {"type": "confirm_end", "id": pending.id, "result": result, "remember": False})
        try:
            if result == "approved":
                await self._merge_now(parent_sid, child, title)
            else:
                self._append(parent_sid, {"type": "tool", "id": call_id, "name": "edit_file", "status": result,
                                          "content": "你没同意合并。"})
                self._note(parent_sid, f"「{title}」没合并：你没同意")
        finally:
            self._merging.pop(child.id, None)

    async def _merge_now(self, parent_sid: str, child: "Session", title: str) -> str:
        self._merging[child.id] = "正在跑测试、合并"
        try:
            event = await self.rt.merge(child.id, run_tests=True)
        except Exception as err:
            self._note(parent_sid, f"「{title}」没合并：{err}")
            raise DispatchError(f"没合并：{err}") from err
        finally:
            self._merging.pop(child.id, None)
        mirrored = {k: v for k, v in event.items() if k not in ("seq", "t")}
        self._append(parent_sid, mirrored)  # 主任务里也有一张合并卡片（测试结果、冲突）
        if event.get("merged"):
            return f"合并了「{title}」：{event.get('reason', '')}，文件：{'、'.join(event.get('files', [])[:40])}"
        return f"没合并「{title}」：{event.get('reason', '')}" + (f"；冲突：{'、'.join(event.get('conflicts', []))}" if event.get("conflicts") else "")

    # ---------- 助手的事转到主任务里 ----------

    def _mirror(self, sid: str, event: "Event") -> None:
        kind = event.get("type")
        if kind not in ("confirm", "confirm_end", "turn_end"):
            return
        if kind in ("turn_end", "confirm_end") and self._unreported.get(sid):
            self._schedule(sid, sid)  # 主任务这一轮做完、你点完确认：攒着的交回报告现在交给它
        session = self.rt.store.get(sid)
        parent_sid = session.events[0].get("parent") if session else None
        if not parent_sid or self.rt.store.get(parent_sid) is None:
            return
        title = self._title(session)
        who = ENGINES.get(str(session.events[0].get("engine")), None)
        name = f"{who.title}（{title}）" if who else title
        if kind == "confirm":
            copy = {k: v for k, v in event.items() if k not in ("seq", "t")}
            copy["call_id"] = f"sub-{sid}-{event.get('call_id', '')}"
            copy["action"] = f"让 {name}{event.get('action', '')}"  # 卡片上念成「要让 Codex（后端）运行命令 …，可以吗？」
            self._append(parent_sid, copy)
        elif kind == "confirm_end":
            self._append(parent_sid, {k: v for k, v in event.items() if k not in ("seq", "t")})
        else:
            reason = str(event.get("reason"))
            text = {"done": "做完了，交回主任务", "error": f"出错了：{event.get('error', '')}", "interrupted": "被打断了",
                    "rejected": "你没同意它要做的操作，停下了", "denied": "被权限挡下，停下了"}.get(reason, f"停下了（{reason}）")
            self._note(parent_sid, f"{name}{text}")
            if should_wake(reason):
                self._unreported.setdefault(parent_sid, {})[sid] = None
                self._schedule(parent_sid, sid)

    # ---------- 交回主任务（handback.py） ----------

    def _seen(self, parent_sid: str, child_sid: str) -> None:
        pending = self._unreported.get(parent_sid)
        if pending is not None:
            pending.pop(child_sid, None)

    def _schedule(self, parent_sid: str, origin: str) -> None:
        """origin 这一轮推完（_mirror 是在那一轮里调的）再看要不要叫醒主任务"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._wake_after(parent_sid, origin))
        self._wakers.add(task)
        task.add_done_callback(self._wakers.discard)

    async def _wake_after(self, parent_sid: str, origin: str) -> None:
        running = self.rt._tasks.get(origin)
        if running is not None and not running.done() and running is not asyncio.current_task():
            await asyncio.wait({running})
        try:
            await self._wake(parent_sid)
        except Exception:  # 叫不醒不能拖垮别的任务
            log.exception("交回主任务 %s 出错", parent_sid)

    async def _wake(self, parent_sid: str) -> None:
        pending = self._unreported.get(parent_sid)
        parent = self.rt.store.get(parent_sid)
        if not pending or parent is None:
            return
        if (self.rt.running(parent_sid) or parent_sid in self.rt._operations or parent_sid in self._pair_jobs
                or parent.pending_confirm() is not None):
            return  # 主任务在忙：等它这一轮结束（_mirror 会再叫）
        ready = [c for c in (self.rt.store.get(i) for i in list(pending)) if c is not None and not self.rt.running(c.id)]
        pair = self._pair(parent)
        members = {str(m.get("id")) for m in pair.get("members", [])} if pair else set()
        if any(self.rt.running(i) for i in members):
            # 一起派的活等都结束了再一起交回（联合验收要两边都在）
            ready = [c for c in ready if c.id not in members]
        if not ready:
            return
        for child in ready:
            pending.pop(child.id, None)
        items = [await asyncio.to_thread(self._handback, child, child.id in members) for child in ready]
        text = report_text(items)
        try:
            agent, request, extra = self.rt._ready(parent_sid, text)
        except Exception as err:  # 分支放弃了、花到上限、同时在做的任务太多……
            self._note(parent_sid, f"没把结果交回主任务：{err}")
            return
        self.rt._begin(parent_sid, agent, request, {**extra, "handback": [c.id for c in ready], "title": report_title(items)})

    def _handback(self, child: "Session", pair: bool) -> Handback:
        end = next((e for e in reversed(child.events) if e["type"] == "turn_end"), {})
        engine = ENGINES.get(str(child.events[0].get("engine")))
        files: list[tuple[str, int | None, int | None]] = []
        if child.branch and not child.branch_closed:
            root = child.workdir if child.workdir.is_dir() else child.cwd
            try:
                found = acceptance.changes(root, child.events, child.branch)["files"]
                files = [(f["path"], f["added"], f["deleted"]) for f in found]
            except Exception as err:  # 工作区没了
                log.warning("看子任务 %s 的改动失败：%s", child.id, err)
        said = next((str(e.get("text", "")) for e in reversed(child.events) if e["type"] == "assistant" and e.get("text")), "")
        branch = (child.branch or {}).get("branch", "") if not child.branch_closed else ""
        return Handback(child.id, self._title(child), engine.title if engine else "艾拉", str(end.get("reason", "")),
                        str(end.get("error", "")), branch, files, acceptance.last_tests(child.events), said, pair)

    def after_restart(self, parent: "Session") -> None:
        """打开主任务时：派出去的活里有做到一半服务就停了的，说一声（不自己重新开工）"""
        ids = [str(e["dispatched"]) for e in parent.events if e["type"] == "note" and e.get("dispatched")]
        ids += [str(m.get("id")) for e in parent.events if e["type"] == "pair_plan" for m in e.get("members", [])]
        cut = []
        for sid in dict.fromkeys(ids):
            child = self.rt.store.get(sid) if not self.rt.running(sid) else None
            if child is not None and child.events[0].get("parent") == parent.id and not child.branch_closed and _cut(child):
                cut.append(self._title(child))
        if cut:
            names = "、".join(f"「{t}」" for t in cut[:5]) + (" 等" if len(cut) > 5 else "")
            self._note(parent.id, f"服务重启前 {names}还没做完：可以打开它接着说，或者让主任务重新派")

    def _note(self, parent_sid: str, text: str) -> None:
        self._append(parent_sid, {"type": "note", "text": text})

    def _append(self, parent_sid: str, event: dict[str, Any]) -> None:
        session = self.rt.store.get(parent_sid)
        if session is None:
            return
        self.rt._publish(parent_sid, session.append(event))


def _cut(session: "Session") -> bool:
    """最后一句话之后没有 turn_end：这一轮没做完进程就没了（和 runner._unfinished 一样）"""
    last_user = max((i for i, e in enumerate(session.events) if e["type"] == "user" and not e.get("imported")), default=-1)
    last_end = max((i for i, e in enumerate(session.events) if e["type"] == "turn_end"), default=-1)
    return last_user > last_end


def _diff(info: BranchInfo, files: list[tuple[str, str]]) -> str:
    """分支上的改动（和基线比），给你看的 diff 由这里生成"""
    from .branch import _at

    wt = Path(info.worktree)
    parts = []
    for _status, path in files:
        old = _at(wt, info.base, path)
        new_path = wt / path
        new = new_path.read_bytes() if new_path.is_file() else None
        a = (old or b"").decode("utf-8", "replace").splitlines()
        b = (new or b"").decode("utf-8", "replace").splitlines()
        parts.append("\n".join(difflib.unified_diff(a, b, f"a/{path}" if old is not None else "/dev/null",
                                                   f"b/{path}" if new is not None else "/dev/null", lineterm="")))
    return "\n".join(p for p in parts if p)[:MAX_DIFF] or "（没有文字改动）"


__all__ = ["Dispatch", "DispatchError", "SERVER_NAME", "TOOLS", "WAIT_MAX"]
