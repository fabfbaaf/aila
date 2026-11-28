"""长任务和整体规划：先出分阶段的计划，你批准后一阶段一阶段做下去，不受一轮步数的限制。

为什么这样设计：
- 计划和进度全在会话的只追加日志里，不另存状态：
  她用 propose_plan 交的计划（tool 事件的 plan 字段）→ 你的决定（plan_decision 事件）→
  每阶段开始的那句话（user 事件带 plan_stage）→ 她报告完成或卡住（stage_done / stage_blocked 的 tool 事件）。
  界面、终端、重启以后接着做，都从日志推出同一份状态（plan_state）
- 每阶段开始、接着做的那句话由代码生成（STAGE_PROMPT），只追加到历史末尾，前面的内容不动，前缀缓存照样命中
- 出计划那一轮是只读的（runner 复用 /review 的只读把关）
- 一轮走满步数还没做完就自动接着做同一阶段，最多续 MAX_CONTINUES 次，还不行就停下等你；
  被打断、花到上限、出错、你没同意某个操作、她说卡住了，都停下，不自己硬做
- 这几个工具不用问（kind 是 meta），它们只是往日志里记一笔
- 服务重启（被强制关掉、电脑重启）：日志里最后一个阶段开了头、后面没有 plan_pause / plan_end，
  就是做到一半进程没了（cut_off）。打开会话时补一条 plan_pause（reason=restart），停在那个阶段等你：
  可以「接着做」，也可以「重做这一阶段」：把这一阶段改过的文件按底片退回到上一阶段做完时（stage_edits），
  记一条 stage_reset，再从头发这一阶段。之前做完、验收过的阶段不动
- 阶段的验收：她做完调 stage_done 是她自己验过；你在交付卡上点「通过 / 退回修改」（acceptance 事件，
  见 acceptance.py）记在刚做完的那个阶段上，界面上能看出哪几个阶段你看过
"""

from dataclasses import dataclass
from typing import Any

from .session import Event

MAX_STAGES = 12
MAX_CONTINUES = 5
TEXT_CHARS = 400

PLAN_PROMPT = """先为下面这个目标出一份分阶段的计划。这一轮只读：可以读代码、搜索、跑查看类的命令，不改文件。

目标：{goal}

怎么做：
1. 先弄清现状：相关代码在哪、怎么组织、怎么构建和测试
2. 用 propose_plan 交计划：2～8 个阶段，每个阶段是一件能单独验证的事，写清目标和怎么验收（跑什么测试、看什么结果）
3. 交完计划用一两句话说明思路和风险，不要开始动手"""

STAGE_PROMPT = """开始第 {n}/{total} 阶段：{title}
目标：{goal}
怎么验收：{verify}
（整个计划的目标：{plan_goal}{done}）
只做这个阶段的事。做完并验收过以后调用 stage_done，说清做了什么、验收结果；做不下去就调用 stage_blocked 说明原因，不要硬做。"""

CONTINUE_PROMPT = """第 {n}/{total} 阶段「{title}」还没做完（{why}），接着做。
做完并验收过以后调用 stage_done；做不下去就调用 stage_blocked 说明原因。"""

RESTART_WHY = "服务重启前做到一半停了，先看看已经改了哪些、测试现在过不过"
REDO_NOTE = "\n（这一阶段之前做到一半，改过的文件已经退回到上一阶段做完时{skipped}；跑过的命令退不回去，留下的东西要自己看一眼。）"


@dataclass
class Stage:
    title: str
    goal: str
    verify: str
    status: str = "pending"  # pending / running / done / blocked
    summary: str = ""
    # 你在交付卡上对这一阶段的验收：做完以后、下一阶段开始前点的「通过」是 True，「退回修改」是 False，没点是 None
    accepted: bool | None = None


@dataclass
class PlanState:
    goal: str
    stages: list[Stage]
    decision: str = "proposed"  # proposed / approved / rejected
    pause_each: bool = False
    current: int | None = None  # 正在做（或该做）的阶段下标
    continues: int = 0  # 当前阶段已经自动接着做了几次
    finished: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "decision": self.decision,
            "pauseEach": self.pause_each,
            "current": self.current,
            "finished": self.finished,
            "stages": [{"title": s.title, "goal": s.goal, "verify": s.verify, "status": s.status, "summary": s.summary,
                        "accepted": s.accepted} for s in self.stages],
        }


def _text(value: Any, limit: int = TEXT_CHARS) -> str:
    return " ".join(str(value or "").split())[:limit]


def parse_plan(args: dict[str, Any]) -> dict[str, Any]:
    """propose_plan 的参数 → 规整过的计划；不合格就抛 ValueError（说给她听的话）"""
    goal = _text(args.get("goal"))
    raw = args.get("stages")
    if not goal:
        raise ValueError("goal 要写整个计划的目标。")
    if not isinstance(raw, list) or not raw:
        raise ValueError("stages 要是阶段列表。")
    if len(raw) > MAX_STAGES:
        raise ValueError(f"阶段太多了：最多 {MAX_STAGES} 个，合并一下。")
    stages = []
    for i, item in enumerate(raw, 1):
        if not isinstance(item, dict) or not _text(item.get("title")):
            raise ValueError(f"第 {i} 个阶段要有 title。")
        stages.append({"title": _text(item.get("title"), 80), "goal": _text(item.get("goal")),
                       "verify": _text(item.get("verify")) or "（没写）"})
    return {"goal": goal, "stages": stages}


def plan_state(events: list[Event]) -> PlanState | None:
    """从日志推出最新那份计划现在做到哪了；没有计划就是 None"""
    state: PlanState | None = None
    last_done: int | None = None  # 刚做完、下一阶段还没开始的那个阶段：你这时候的验收算在它上面
    for e in events:
        kind = e.get("type")
        if kind == "tool" and e.get("name") == "propose_plan" and isinstance(e.get("plan"), dict):
            plan = e["plan"]
            state = PlanState(plan["goal"], [Stage(s["title"], s["goal"], s["verify"]) for s in plan["stages"]])
        elif state is None:
            continue
        elif kind == "plan_decision":
            state.decision = "approved" if e.get("approved") else "rejected"
            state.pause_each = bool(e.get("pause_each"))
            if state.decision == "approved" and state.current is None:
                state.current = _next(state)
        elif kind == "user" and isinstance(e.get("plan_stage"), int) and state.decision == "approved":
            n = e["plan_stage"]
            if 0 <= n < len(state.stages):
                state.continues = state.continues + 1 if (state.current == n and state.stages[n].status == "running") else 0
                state.current = n
                last_done = None
                if state.stages[n].status != "done":
                    state.stages[n].status = "running"
        elif kind == "tool" and e.get("name") in ("stage_done", "stage_blocked") and isinstance(e.get("stage"), int):
            n = e["stage"]
            if 0 <= n < len(state.stages):
                state.stages[n].status = "done" if e["name"] == "stage_done" else "blocked"
                state.stages[n].summary = str(e.get("summary", ""))
                if e["name"] == "stage_done":
                    state.current = _next(state)
                    state.continues = 0
                    last_done = n
        elif kind == "stage_reset" and isinstance(e.get("stage"), int) and state.decision == "approved":
            n = e["stage"]
            if 0 <= n < len(state.stages):
                state.stages[n].status, state.stages[n].summary, state.stages[n].accepted = "pending", "", None
                state.current, state.continues, last_done = n, 0, None
        elif kind == "acceptance" and last_done is not None:
            state.stages[last_done].accepted = e.get("verdict") == "accepted"
    if state and state.decision == "approved":
        state.finished = all(s.status == "done" for s in state.stages)
        if state.finished:
            state.current = None
    return state


def _next(state: PlanState) -> int | None:
    return next((i for i, s in enumerate(state.stages) if s.status != "done"), None)


_PLAN_MARKS = ("plan_decision", "plan_pause", "plan_end", "stage_reset")


def cut_off(events: list[Event]) -> bool:
    """按计划做到一半进程没了：最后一个阶段开了头，后面没有停下（plan_pause）、做完（plan_end）的记录。
    只在会话刚加载、肯定没在做的时候用（在做的时候日志也是这个样子）"""
    state = plan_state(events)
    if state is None or state.decision != "approved" or state.finished or state.current is None:
        return False
    for e in reversed(events):
        if e.get("type") in _PLAN_MARKS:
            return False
        if e.get("type") == "user" and isinstance(e.get("plan_stage"), int):
            return True
    return False


def restart_text(state: PlanState) -> str:
    n = state.current or 0
    done = [i for i, s in enumerate(state.stages) if s.status == "done"]
    after = f"，前面 {len(done)} 个阶段做完了" if done else ""
    if state.stages[n].status == "running":
        return f"服务重启了，第 {n + 1} 阶段做到一半停了{after}"
    return f"服务重启了，停在第 {n + 1} 阶段开始之前{after}"


def resumed_after_restart(events: list[Event]) -> bool:
    """最近一次停下是因为服务重启（还没接着做）"""
    last = next((e for e in reversed(events) if e.get("type") in _PLAN_MARKS), None)
    return bool(last and last.get("type") == "plan_pause" and last.get("reason") == "restart")


def stage_edits(events: list[Event], n: int) -> tuple[list[Event], bool]:
    """第 n 阶段这一次（上一阶段做完、上次重做之后）改过的文件记录，按日志先后；撤销过的那几轮不算。
    第二个值：这期间跑过命令没有（命令的效果退不回去，要告诉你）"""
    start = 0
    for i, e in enumerate(events):
        kind = e.get("type")
        if (kind == "tool" and e.get("name") == "stage_done" and isinstance(e.get("stage"), int)) or kind == "stage_reset":
            start = i + 1
    first = next((i for i in range(start, len(events)) if events[i].get("type") == "user"
                  and events[i].get("plan_stage") == n), None)
    if first is None:
        return [], False
    undone = {e.get("turn_seq") for e in events if e.get("type") == "undo"}
    edits: list[Event] = []
    commands = False
    turn: int | None = None
    for e in events[first:]:
        if e.get("type") == "user":
            turn = e.get("seq")
        if e.get("type") != "tool" or e.get("status") != "ok" or turn in undone:
            continue
        if e.get("name") in ("edit_file", "write_file") and e.get("file"):
            edits.append(e)
        elif e.get("name") == "run_command":
            commands = True
    return edits, commands


def stage_message(state: PlanState, n: int, continuing: str = "") -> tuple[str, dict[str, Any]]:
    """第 n 阶段开始（或接着做）时发给她的话，和日志里 user 事件带的标记"""
    stage = state.stages[n]
    total = len(state.stages)
    if continuing:
        text = CONTINUE_PROMPT.format(n=n + 1, total=total, title=stage.title, why=continuing)
        title = f"接着做第 {n + 1} 阶段：{stage.title}"
    else:
        done = [f"{i + 1}. {s.title}" for i, s in enumerate(state.stages) if s.status == "done"]
        text = STAGE_PROMPT.format(n=n + 1, total=total, title=stage.title, goal=stage.goal or stage.title,
                                   verify=stage.verify, plan_goal=state.goal,
                                   done=f"；已经做完的阶段：{'、'.join(done)}" if done else "")
        title = f"第 {n + 1}/{total} 阶段：{stage.title}"
    return text, {"plan_stage": n, "title": title[:60]}


def plan_title(goal: str) -> str:
    return f"出方案：{_text(goal, 50)}"


def plan_tools(events: list[Event]) -> list:  # -> list[CodingTool]
    """propose_plan、stage_done、stage_blocked（放在工具列表里固定的位置）。events 是这个会话的日志（同一个列表，随时是最新的）"""
    from .tools import CodingTool, Outcome, _obj, _str

    async def propose(args: dict[str, Any]) -> Outcome:
        try:
            plan = parse_plan(args)
        except ValueError as err:
            return Outcome(str(err), ok=False)
        lines = [f"{i}. {s['title']}（验收：{s['verify']}）" for i, s in enumerate(plan["stages"], 1)]
        return Outcome("计划交给用户了，等用户批准后会一阶段一阶段发给你：\n" + "\n".join(lines), extra={"plan": plan})

    def current_stage() -> int | None:
        state = plan_state(events)
        if not state or state.decision != "approved" or state.finished or state.current is None:
            return None
        return state.current if state.stages[state.current].status == "running" else None

    async def done(args: dict[str, Any]) -> Outcome:
        n = current_stage()
        if n is None:
            return Outcome("现在没有在做的阶段，不用调用这个。", ok=False)
        summary = _text(args.get("summary"), 600)
        verified = _text(args.get("verified"), 600)
        if not summary:
            return Outcome("summary 要写这个阶段做了什么。", ok=False)
        return Outcome(f"第 {n + 1} 阶段记为完成。", extra={"stage": n, "summary": f"{summary}（验收：{verified or '没写'}）"})

    async def blocked(args: dict[str, Any]) -> Outcome:
        n = current_stage()
        if n is None:
            return Outcome("现在没有在做的阶段，不用调用这个。", ok=False)
        reason = _text(args.get("reason"), 600)
        if not reason:
            return Outcome("reason 要写清卡在哪了。", ok=False)
        return Outcome(f"第 {n + 1} 阶段记为卡住，等用户决定。", extra={"stage": n, "summary": reason})

    stage_item = {"type": "object", "properties": {
        "title": _str("阶段名，一句话"), "goal": _str("这个阶段要做成什么"), "verify": _str("怎么验收：跑什么测试、看什么结果")},
        "required": ["title", "goal", "verify"]}
    return [
        CodingTool(
            "propose_plan",
            "把分阶段的计划交给用户批准（用户让你先出方案时用）。每个阶段是一件能单独验证的事；交完不要开始动手，等用户批准。",
            _obj({"goal": _str("整个计划的目标"), "stages": {"type": "array", "items": stage_item,
                                                          "description": f"阶段列表，2～8 个为宜，最多 {MAX_STAGES} 个"}},
                 ["goal", "stages"]),
            propose, lambda _: "meta", lambda a: "交计划给你看",
        ),
        CodingTool(
            "stage_done",
            "按计划做的时候：当前阶段做完、验收过了，调用它记为完成，系统会接着发下一阶段。",
            _obj({"summary": _str("这个阶段做了什么"), "verified": _str("怎么验收的、结果")}, ["summary", "verified"]),
            done, lambda _: "meta", lambda a: "这个阶段做完了",
        ),
        CodingTool(
            "stage_blocked",
            "按计划做的时候：当前阶段做不下去了（缺信息、要用户决定、反复失败），调用它停下来，说清原因。",
            _obj({"reason": _str("卡在哪了，需要用户做什么")}, ["reason"]),
            blocked, lambda _: "meta", lambda a: "这个阶段卡住了",
        ),
    ]

