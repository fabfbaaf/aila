"""「定时任务」技能包：对话里让她设定时、自动触发的编程任务（「每天早上 9 点帮我跑一下测试，没过就查原因」）。

设定时任务等于给她一条以后自己去做的规矩，所以每次都要你确认（risk="write"）；
确认时给你看的「什么时候、在哪个项目、做什么、什么权限」由代码从参数拼（Schedule.describe），不用她自己的描述。
项目只认登记过、做过任务的；权限默认只读。规则见 app/runtime/schedules.py。
"""

from collections.abc import Callable
from datetime import datetime
from typing import Any

from ..runtime.schedules import Schedule, ScheduleStore, validate
from . import Skill, Tool, ToolError

WEEK = "星期几：0 是周一 … 6 是周日；不填是每天，工作日是 [0,1,2,3,4]"


def schedule_skill(store: Callable[[], ScheduleStore], projects: Callable[[], list[tuple[str, str]]]) -> Skill:
    def project_of(args: dict[str, Any]) -> str | None:
        name = str(args.get("project", "")).strip().lower()
        found = projects()
        exact = [p for n, p in found if n.lower() == name]
        loose = [p for n, p in found if name and name in n.lower()]
        hits = exact or loose or ([found[0][1]] if len(found) == 1 and not name else [])
        return hits[0] if len(hits) == 1 else None

    def build(args: dict[str, Any]) -> Schedule:
        kind = "interval" if args.get("hours") else "daily"
        days = args.get("days")
        return Schedule(
            id="", name=str(args.get("name", "")).strip()[:40], project=project_of(args) or "",
            prompt=str(args.get("prompt", "")).strip(), kind=kind, time=str(args.get("time", "09:00")).strip(),
            days=[int(d) for d in days] if isinstance(days, list) and days else list(range(7)),
            hours=int(args.get("hours") or 24), action="tests" if args.get("only_if_tests_fail") else "run",
            mode=str(args.get("mode") or "read_only"),
        )

    def check(args: dict[str, Any]) -> str | None:
        if project_of(args) is None:
            names = "、".join(n for n, _ in projects()[:8]) or "（还没有）"
            return f"不知道是哪个项目（登记过、做过任务的有：{names}）。问用户是哪一个。"
        try:
            return validate(build(args))
        except (TypeError, ValueError):
            return "参数不对：days 是 0~6 的数字列表，hours 是整数。"

    async def create(args: dict[str, Any]) -> str:
        try:
            s = store().add(build(args), datetime.now().astimezone())
        except ValueError as err:
            raise ToolError(str(err)) from err
        return f"设好了：{s.describe()}。设置 → 编程 →「定时任务」里能改、停、删。"

    async def list_(_: dict[str, Any]) -> str:
        items = store().items
        if not items:
            return "还没有定时任务。"
        return "\n".join(f"- {s.name}：{s.describe()}{'' if s.enabled else '（停着）'}；上次：{s.last_result or '还没跑过'}"
                         for s in items)

    return Skill("schedule", "定时任务", "让她定时、自动地做编程任务（每天跑测试、没过就查原因）", [
        Tool(
            "schedule_task",
            "设一个定时或自动触发的编程任务：每天几点（可选星期几）或每隔几小时，在用户的项目里做一件事；"
            "only_if_tests_fail=true 时先跑项目的测试，没过才开任务查原因。会先问用户。权限默认只读，用户明确要她改代码时才用 ask。",
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "短名字，比如「早上跑测试」"},
                    "project": {"type": "string", "description": "项目名"},
                    "prompt": {"type": "string", "description": "到点了让她做什么（用户的原话加上必要的细节）"},
                    "time": {"type": "string", "description": "每天几点，写成 09:00"},
                    "days": {"type": "array", "items": {"type": "integer"}, "description": WEEK},
                    "hours": {"type": "integer", "description": "每隔几小时（填了就不按每天几点）"},
                    "only_if_tests_fail": {"type": "boolean", "description": "先跑测试，没过才做"},
                    "mode": {"type": "string", "enum": ["read_only", "ask", "auto"], "description": "权限，默认 read_only"},
                },
                "required": ["name", "project", "prompt"],
            },
            create,
            lambda a: f"正在设定时任务「{a.get('name', '')}」",
            risk="write",
            action="设一个定时任务",
            detail=lambda a: f"{build(a).describe()}\n项目：{project_of(a) or '（对不上）'}",
            validate=check,
        ),
        Tool("list_schedules", "看看现在有哪些定时任务、上次跑的结果。", {"type": "object", "properties": {}}, list_,
             lambda _: "正在看定时任务"),
    ])
