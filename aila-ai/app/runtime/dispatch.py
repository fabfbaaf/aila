"""跨 Agent 派工：本机的 Codex / Claude Code 当主 Agent 时，能把一块活派给另一个本机 Agent（或者同一种再开一个）。

用户说「前端你做，后端交给 Codex」，主 Agent 用艾拉给它的工具派出去：
- 艾拉给主 Agent 挂一个本机的 MCP 服务（POST /agent/bridge，带这个会话专用的令牌），里面是派工的几个工具
- 派出去的是一个普通的编程会话：开在独立的 git 工作区（新分支，branch.py），用户的代码在合并前一个字都不动；
  会话开头记着是谁派的（parent），任务页左边列表里也看得到，能点进去看
- 助手要你同意的操作（确认卡片）原样转到主任务里：在主任务里就能点；它做完、出错也在主任务里说一声
- 收回：主 Agent 叫 merge_work。合并 = 写进用户的代码，所以按主任务的权限模式问你（给你看的 diff 由这里生成）；
  合并前跑项目自带的测试，有冲突不合并（主 Agent 同时在原地改了同一个文件就算冲突）。问你的时候不卡住主 Agent：
  先告诉它「在等用户同意」，同意以后在后台合并，结果用 work_status 看
- 工具调用有超时（Codex 默认一分钟），所以「等它做完」一次最多等 50 秒，没做完就说还在做

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
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .branch import BranchInfo, changes
from .localagents import ENGINES

if TYPE_CHECKING:
    from .runner import Runtime
    from .session import Event, Session

log = logging.getLogger("aila.dispatch")

SERVER_NAME = "aila"
WAIT_MAX = 50  # 秒：一次最多等这么久（主 Agent 的工具调用超时一般是 60 秒）
MAX_DIFF = 60_000
MAX_SAID = 1_500

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
                "agent": {"type": "string", "enum": list(ENGINES), "description": "派给谁：codex 或 claude"},
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


class DispatchError(Exception):
    """说给主 Agent 听的（它会转告用户）"""


class Dispatch:
    def __init__(self, runtime: "Runtime", base_url: str | None) -> None:
        self.rt = runtime
        self.base_url = base_url  # 艾拉自己的地址（http://127.0.0.1:8765）；没有就不给主 Agent 派工工具
        self._tokens: dict[str, str] = {}  # 令牌 → 主会话
        self._merging: dict[str, str] = {}  # 正在等同意或正在合并的助手会话 → 现在的情况
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

    async def call(self, parent_sid: str, name: str, args: dict[str, Any]) -> str:
        if name == "delegate":
            return await self.delegate(parent_sid, str(args.get("agent", "")), str(args.get("title", "")), str(args.get("task", "")))
        child = self._child(parent_sid, str(args.get("id", "")))
        if name == "work_status":
            return self.status(child)
        if name == "wait_work":
            seconds = max(1, min(WAIT_MAX, int(args.get("seconds") or WAIT_MAX)))
            deadline = time.monotonic() + seconds
            while self.rt.running(child.id) and time.monotonic() < deadline and not child.waiting():
                await asyncio.sleep(0.5)
            return self.status(child)
        if name == "merge_work":
            return await self.merge(parent_sid, child)
        if name == "discard_work":
            if self.rt.running(child.id):
                raise DispatchError("它还在做，先等它做完（或者让用户在任务页里打断）再放弃。")
            await self.rt.discard(child.id)
            self._note(parent_sid, f"不要「{self._title(child)}」了：分支删了，代码保持原样")
            return "放弃了：分支和工作区都删了，用户的代码保持原样。"
        raise DispatchError(f"没有叫 {name} 的工具。")

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
        self._note(parent_sid, f"派给 {ENGINES[agent].title}：「{title}」，在分支 {branch} 的独立工作区里做")
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
            text = {"done": "做完了，等主 Agent 收回", "error": f"出错了：{event.get('error', '')}", "interrupted": "被打断了",
                    "rejected": "你没同意它要做的操作，停下了", "denied": "被权限挡下，停下了"}.get(reason, f"停下了（{reason}）")
            self._note(parent_sid, f"{name}{text}")

    def _note(self, parent_sid: str, text: str) -> None:
        self._append(parent_sid, {"type": "note", "text": text})

    def _append(self, parent_sid: str, event: dict[str, Any]) -> None:
        session = self.rt.store.get(parent_sid)
        if session is None:
            return
        self.rt._publish(parent_sid, session.append(event))


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
