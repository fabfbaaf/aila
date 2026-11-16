"""「提醒」技能包：时间由代码算、不对的拒绝、记好后推 reminder 事件给桌面端"""

from datetime import datetime, timedelta, timezone

import pytest

from app.agent import run_agent
from app.llm import MockModel
from app.memory import describe
from app.tools import SkillRegistry
from app.tools.reminders import describe_when, reminder_skill, resolve_time

TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 11, 16, 14, 30, 20, tzinfo=TZ)


def test_minutes_from_now() -> None:
    assert resolve_time(NOW, in_minutes=25) == NOW + timedelta(minutes=25)
    assert resolve_time(NOW, in_minutes="90") == NOW + timedelta(minutes=90)


def test_clock_time_today_or_tomorrow() -> None:
    assert resolve_time(NOW, at="15:00") == datetime(2026, 11, 16, 15, 0, tzinfo=TZ)
    # 只说了几点、今天已经过了：明天
    assert resolve_time(NOW, at="9:30") == datetime(2026, 11, 17, 9, 30, tzinfo=TZ)
    assert resolve_time(NOW, at="10：00") == datetime(2026, 11, 17, 10, 0, tzinfo=TZ)


def test_full_date() -> None:
    assert resolve_time(NOW, at="2026-11-20 09:30") == datetime(2026, 11, 20, 9, 30, tzinfo=TZ)
    assert resolve_time(NOW, at="2026-11-20T09:30") == datetime(2026, 11, 20, 9, 30, tzinfo=TZ)


@pytest.mark.parametrize(("kwargs", "why"), [
    ({"in_minutes": 0}, "至少"),
    ({"in_minutes": "半小时"}, "分钟数"),
    ({"at": "2026-11-16 08:00"}, "已经过了"),
    ({"at": "2027-03-01 08:00"}, "太远"),
    ({"at": "25:00"}, "看不懂"),
    ({"at": "明天上午"}, "看不懂"),
    ({}, "要给"),
])
def test_bad_times_are_explained(kwargs: dict, why: str) -> None:
    result = resolve_time(NOW, **kwargs)
    assert isinstance(result, str) and why in result


def test_describe_when() -> None:
    assert describe_when(NOW, datetime(2026, 11, 16, 15, 0, tzinfo=TZ)) == "今天 15:00"
    assert describe_when(NOW, datetime(2026, 11, 17, 10, 0, tzinfo=TZ)) == "明天 10:00"
    assert describe_when(NOW, datetime(2026, 11, 20, 9, 30, tzinfo=TZ)) == "11 月 20 日（星期五）09:30"


def test_background_has_the_time() -> None:
    assert "现在 14:30" in describe([], NOW)


@pytest.mark.anyio
async def test_chat_emits_a_reminder_event() -> None:
    """对假模型说「25 分钟后提醒我喝水」：工具算好时间，推一个 reminder 事件，她回一句"""
    box = SkillRegistry([reminder_skill()]).toolbox()
    before = datetime.now().astimezone()
    events = [e async for e in run_agent(MockModel(delay=0), [{"role": "user", "content": "25 分钟后提醒我喝水"}], box)]
    sent = [e for e in events if e["type"] == "reminder"]
    assert len(sent) == 1 and sent[0]["text"] == "喝水"
    at = datetime.fromisoformat(sent[0]["at"])
    assert at.tzinfo is not None
    assert timedelta(minutes=24) < at - before < timedelta(minutes=26)
    said = "".join(e["text"] for e in events if e["type"] == "text")
    assert "叫你" in said


@pytest.mark.anyio
async def test_bad_time_sends_nothing() -> None:
    box = SkillRegistry([reminder_skill()]).toolbox()
    from app.llm import ToolCall

    class Once:
        name = "once"

        def __init__(self) -> None:
            self.n = 0

        async def stream(self, messages, tools=None):  # type: ignore[no-untyped-def]
            self.n += 1
            if self.n == 1:
                yield ToolCall("r1", "set_reminder", '{"text": "开会", "at": "2020-01-01 10:00"}')
            else:
                yield "好"

    events = [e async for e in run_agent(Once(), [{"role": "user", "content": "提醒我开会"}], box)]  # type: ignore[arg-type]
    assert not [e for e in events if e["type"] == "reminder"]
