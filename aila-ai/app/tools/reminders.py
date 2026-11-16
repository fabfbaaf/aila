"""「提醒」技能包：「25 分钟后叫我」「明天上午十点提醒我交周报」。

AI 服务不存用户数据：她只负责把用户说的时间算成具体时刻，推一个 reminder 事件给桌面端，
桌面端主进程记下来（userData/reminders.json）、到点让她在桌面上叫你（她藏起来时改用系统通知）。

时间由代码算，不让大模型自己做加法：模型只填「几分钟后」或者「几点」，这里换成带时区的具体时间，
已经过了的、超过一个月的直接拒绝，告诉模型原因。
"""

import re
from datetime import datetime, timedelta
from typing import Any

from . import Skill, Tool, emit

MAX_AHEAD = timedelta(days=31)
MAX_TEXT = 200
WEEKDAYS = "一二三四五六日"


def resolve_time(now: datetime, in_minutes: Any = None, at: Any = None) -> datetime | str:
    """「几分钟后」或「几点」→ 具体时间（和 now 同一个时区）；不对的话返回说给模型听的原因"""
    if in_minutes not in (None, ""):
        try:
            minutes = float(in_minutes)
        except (TypeError, ValueError):
            return "in_minutes 要是分钟数，比如 25。"
        if minutes < 1:
            return "至少要 1 分钟以后。"
        target = now + timedelta(minutes=minutes)
    elif at not in (None, ""):
        parsed = _parse_at(now, str(at).strip())
        if isinstance(parsed, str):
            return parsed
        target = parsed
    else:
        return "要给 in_minutes（几分钟后）或者 at（几点，比如 15:00 或 2026-11-17 10:00）。"
    if target <= now:
        return "这个时间已经过了。问问用户是不是说的明天。"
    if target - now > MAX_AHEAD:
        return "太远了：最多提前一个月。"
    return target.replace(second=0, microsecond=0) if in_minutes in (None, "") else target


def _parse_at(now: datetime, text: str) -> datetime | str:
    only_time = re.fullmatch(r"(\d{1,2})[:：](\d{2})", text)
    if only_time:
        hour, minute = int(only_time.group(1)), int(only_time.group(2))
        if hour > 23 or minute > 59:
            return f"看不懂这个时间：{text}"
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        # 只说了几点、今天已经过了：当成明天
        return target if target > now else target + timedelta(days=1)
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=now.tzinfo)
        except ValueError:
            continue
    return f"看不懂这个时间：{text}。用 15:00 或者 2026-11-17 10:00 这样的写法。"


def describe_when(now: datetime, target: datetime) -> str:
    """今天 15:00 / 明天 10:00 / 11 月 20 日（星期四）09:30"""
    clock = f"{target:%H:%M}"
    days = (target.date() - now.date()).days
    if days == 0:
        return f"今天 {clock}"
    if days == 1:
        return f"明天 {clock}"
    return f"{target.month} 月 {target.day} 日（星期{WEEKDAYS[target.weekday()]}）{clock}"


def reminder_skill() -> Skill:
    async def set_reminder(args: dict[str, Any]) -> str:
        text = str(args.get("text", "")).strip()[:MAX_TEXT]
        if not text:
            return "提醒的内容是空的：要提醒用户做什么？"
        now = datetime.now().astimezone()
        target = resolve_time(now, args.get("in_minutes"), args.get("at"))
        if isinstance(target, str):
            return target
        emit({"type": "reminder", "at": target.isoformat(timespec="seconds"), "text": text})
        return (f"记好了：{describe_when(now, target)} 提醒用户「{text}」。"
                "简单回一句就好（可以说一下时间）；到点时桌面会叫用户，你不用再做什么。")

    tool = Tool(
        "set_reminder",
        "用户让你到时间提醒他（「25 分钟后叫我」「三点提醒我开会」「明天上午十点提醒我交周报」）时用。"
        "相对时间填 in_minutes；具体时间填 at。时间由系统算，不要自己换算成别的写法。",
        {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "到点时提醒什么，用用户的话，简短（比如「喝水」「三点的周会」）"},
                "in_minutes": {"type": "number", "description": "几分钟以后（「半小时后」填 30，「两小时后」填 120）"},
                "at": {"type": "string", "description": "几点：今天或明天的「15:00」，或者带日期的「2026-11-17 10:00」"},
            },
            "required": ["text"],
        },
        set_reminder,
        lambda _: "正在记提醒",
    )
    return Skill("reminders", "提醒", "让她到点提醒你：「25 分钟后叫我」「明天十点提醒我交周报」", [tool])
