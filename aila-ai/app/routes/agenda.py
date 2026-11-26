"""日历和邮件的本机接口：桌面端早上问候、会前提醒、设置页「试试」用（只读，规则见 app/agenda.py、app/mail.py）。

和别的本机接口一样只认本机（security.py）。订阅链接和授权码不从这里回传。
"""

import asyncio
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException

from .. import deps
from ..agenda import CalendarError
from ..mail import MailError

router = APIRouter(prefix="/agenda")


async def _events(start: datetime, end: datetime) -> dict[str, Any]:
    cal = deps.get_calendar()
    if cal is None:
        return {"configured": False, "events": [], "error": ""}
    try:
        events = await cal.between(start, end)
    except CalendarError as err:
        return {"configured": True, "events": [], "error": str(err)}
    return {"configured": True, "events": [e.public() for e in events], "error": cal.error}


@router.get("/today")
async def today() -> dict[str, Any]:
    now = datetime.now().astimezone()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return await _events(start, start + timedelta(days=1))


@router.get("/upcoming")
async def upcoming(minutes: int = 10) -> dict[str, Any]:
    """接下来 minutes 分钟内开始的（会前提醒用；全天的不算）"""
    now = datetime.now().astimezone()
    got = await _events(now, now + timedelta(minutes=max(1, min(minutes, 120))))
    got["events"] = [e for e in got["events"] if not e["allDay"] and e["start"] >= now.isoformat()]
    return got


@router.post("/test-calendar")
async def test_calendar() -> dict[str, Any]:
    cal = deps.get_calendar()
    if cal is None:
        raise HTTPException(409, "还没填日历订阅链接。")
    now = datetime.now().astimezone()
    try:
        events = await cal.between(now, now + timedelta(days=7))
    except CalendarError as err:
        raise HTTPException(502, str(err)) from err
    return {"ok": True, "count": len(events)}


@router.post("/test-mail")
async def test_mail() -> dict[str, Any]:
    if not deps.mail_ready():
        raise HTTPException(409, "还没填邮箱和授权码。")
    try:
        mails = await asyncio.to_thread(deps.read_mail, 30, False, 7)
    except MailError as err:
        raise HTTPException(502, str(err)) from err
    return {"ok": True, "count": len(mails), "unread": sum(1 for m in mails if m.unread)}
