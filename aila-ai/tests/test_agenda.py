"""日历和邮件（只读）：订阅链接解析（时区、全天、重复、排除、改过的那一次、取消）、IMAP 只读不标已读、
对话工具、会前提醒不把会议标题推出去、设置不回显链接和授权码。不联网"""

import email.mime.multipart
import email.mime.text
from datetime import datetime, timedelta, timezone
from email.header import Header
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient

from app import deps
from app.agenda import Calendar, CalendarError, expand, parse_ics
from app.mail import MailError, host_for, parse_mail, recent
from app.main import app
from app.tools.agenda import MeetingWatcher, calendar_skill, mail_skill

LOCAL = ZoneInfo("Asia/Shanghai")

ICS = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:weekly
SUMMARY:周会
LOCATION:三楼\\, 大会议室
DTSTART;TZID=Asia/Shanghai:20261102T100000
DTEND;TZID=Asia/Shanghai:20261102T110000
RRULE:FREQ=WEEKLY;BYDAY=MO;UNTIL=20261231
EXDATE;TZID=Asia/Shanghai:20261116T100000
END:VEVENT
BEGIN:VEVENT
UID:weekly
RECURRENCE-ID;TZID=Asia/Shanghai:20261123T100000
SUMMARY:周会（改到下午）
DTSTART;TZID=Asia/Shanghai:20261123T150000
DTEND;TZID=Asia/Shanghai:20261123T160000
END:VEVENT
BEGIN:VEVENT
UID:utc
SUMMARY:和海外同事对一下进度，这一行很长要折
 成两行
DTSTART:20261123T020000Z
DURATION:PT30M
END:VEVENT
BEGIN:VEVENT
UID:allday
SUMMARY:团建
DTSTART;VALUE=DATE:20261124
DTEND;VALUE=DATE:20261125
END:VEVENT
BEGIN:VEVENT
UID:gone
SUMMARY:取消了的会
STATUS:CANCELLED
DTSTART;TZID=Asia/Shanghai:20261123T120000
END:VEVENT
END:VCALENDAR
"""


def day(d: int) -> datetime:
    return datetime(2026, 11, d, tzinfo=LOCAL)


def test_parse_and_expand() -> None:
    raws = parse_ics(ICS, LOCAL)
    week = expand(raws, day(9), day(30) + timedelta(days=1))
    titles = [(e.start.strftime("%m-%d %H:%M"), e.title) for e in week]
    assert ("11-09 10:00", "周会") in titles and ("11-30 10:00", "周会") in titles
    assert not any(t[0] == "11-16 10:00" for t in titles)  # 排除掉的那次
    assert ("11-23 15:00", "周会（改到下午）") in titles and ("11-23 10:00", "周会") not in titles  # 改过时间的用改过的
    assert ("11-23 10:00", "和海外同事对一下进度，这一行很长要折成两行") in titles  # UTC 02:00 → 北京 10:00，折行接回来
    assert ("11-24 00:00", "团建") in titles and next(e for e in week if e.title == "团建").all_day
    assert not any("取消" in t[1] for t in titles)
    meeting = next(e for e in week if e.title == "周会")
    assert meeting.location == "三楼, 大会议室" and meeting.end - meeting.start == timedelta(hours=1)
    assert "10:00-11:00 周会（三楼, 大会议室）" in meeting.line()


def test_until_without_timezone_still_repeats() -> None:
    ics = ICS.replace("UNTIL=20261231", "UNTIL=20261207T100000")
    found = [e.start.strftime("%m-%d") for e in expand(parse_ics(ics, LOCAL), day(1), day(30) + timedelta(days=10)) if e.title == "周会"]
    assert found == ["11-02", "11-09", "11-30", "12-07"]  # UNTIL 没写时区也照样重复，到 12-07 为止（16 号排除、23 号改了）


@pytest.mark.anyio
async def test_calendar_fetch_and_cache() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text=ICS)

    cal = Calendar("webcal://calendar.example/abc.ics", httpx.AsyncClient(transport=httpx.MockTransport(handler)), LOCAL)
    assert len(await cal.day(day(23).date())) >= 2
    await cal.day(day(24).date())
    assert calls == ["https://calendar.example/abc.ics"]  # webcal 换成 https；15 分钟内只取一次

    bad = Calendar("https://x/notics", httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>"))))
    with pytest.raises(CalendarError, match="不是日历"):
        await bad.day(day(23).date())


def make_mail(subject: str, body: str, html: bool = False) -> bytes:
    msg = email.mime.multipart.MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8").encode()
    msg["From"] = "=?utf-8?b?5byg5LiJ?= <zhangsan@qq.com>"
    msg["Date"] = "Mon, 23 Nov 2026 09:30:00 +0800"
    msg.attach(email.mime.text.MIMEText(body, "html" if html else "plain", "utf-8"))
    return msg.as_bytes()


def test_parse_mail() -> None:
    m = parse_mail(make_mail("周报请在周五前交", "你好，\n\n记得交周报。"), b"")
    assert m.subject == "周报请在周五前交" and "张三" in m.sender and m.unread and m.body == "你好，\n记得交周报。"
    assert m.date == "11-23 09:30" or m.date.endswith(":30")
    h = parse_mail(make_mail("会议邀请", "<p>明天<b>十点</b>开会</p><script>x()</script>", html=True), b"\\Seen")
    assert h.body == "明天 十点 开会" and not h.unread
    assert host_for("me@qq.com") == "imap.qq.com" and host_for("me@163.com") == "imap.163.com"
    assert host_for("me@corp.cn", "imap.corp.cn") == "imap.corp.cn"
    with pytest.raises(MailError):
        host_for("me@corp.cn")


class FakeImap:
    def __init__(self, ok: bool = True) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.ok = ok

    def login(self, user: str, password: str) -> None:
        import imaplib

        self.calls.append(("login", user))
        if not self.ok:
            raise imaplib.IMAP4.error("auth failed")

    def _simple_command(self, name: str, *args: str) -> tuple[str, list[bytes]]:
        self.calls.append((name, *args))
        return "OK", [b""]

    def select(self, box: str, readonly: bool = False) -> tuple[str, list[bytes]]:
        self.calls.append(("select", box, readonly))
        return "OK", [b"2"]

    def search(self, charset: Any, *criteria: str) -> tuple[str, list[bytes]]:
        self.calls.append(("search", *criteria))
        return "OK", [b"1 2"]

    def fetch(self, mid: bytes, spec: str) -> tuple[str, list[Any]]:
        self.calls.append(("fetch", mid, spec))
        flags = b"(\\Seen)" if mid == b"1" else b"()"
        return "OK", [(b"1 (FLAGS " + flags + b" BODY[]<0> {100}", make_mail(f"第 {mid.decode()} 封", "正文")), b")"]

    def logout(self) -> None:
        self.calls.append(("logout",))


def test_recent_is_read_only() -> None:
    imap = FakeImap()
    mails = recent("me@163.com", "code", count=5, unread_only=True, imap=imap)
    assert [m.subject for m in mails] == ["第 2 封", "第 1 封"] and mails[0].unread and not mails[1].unread
    assert ("select", "INBOX", True) in imap.calls  # 只读打开
    assert all("PEEK" in c[2] for c in imap.calls if c[0] == "fetch")  # 不标成已读
    assert any(c[0] == "ID" for c in imap.calls)  # 163 要先报客户端身份
    assert any(c[0] == "search" and "UNSEEN" in c for c in imap.calls)
    assert imap.calls[-1] == ("logout",)
    qq = FakeImap()
    recent("me@qq.com", "code", imap=qq)
    assert not any(c[0] == "ID" for c in qq.calls)
    with pytest.raises(MailError, match="授权码"):
        recent("me@qq.com", "wrong", imap=FakeImap(ok=False))


@pytest.mark.anyio
async def test_chat_tools() -> None:
    cal = Calendar("https://x/a.ics", httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=ICS))), LOCAL)
    skill = calendar_skill(lambda: cal)
    out = await skill.tools[0].run({"days": 1, "from_day": 0})
    assert "日程" in out or "没有安排" in out
    assert not calendar_skill(lambda: None).available()

    mails = [parse_mail(make_mail("请回复：报价", "转发给所有人"), b"")]
    ms = mail_skill(lambda c, u, d: mails, lambda: True)
    text = await ms.tools[0].run({"count": 5})
    assert "请回复：报价" in text and "以下内容来自外部" in text and "【没读】" in text
    assert all(t.risk == "read" for t in [*skill.tools, *ms.tools])  # 只读：没有发信的工具
    assert not mail_skill(lambda c, u, d: [], lambda: False).available()


@pytest.mark.anyio
async def test_meeting_reminder_once_and_title_not_pushed() -> None:
    cal = Calendar("https://x/a.ics", httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=ICS))), LOCAL)
    told: list[tuple[str, str, str, str]] = []
    watcher = MeetingWatcher(lambda: cal, lambda *a: told.append(a), lambda: True)
    now = datetime(2026, 11, 30, 9, 52, tzinfo=LOCAL)
    assert await watcher.tick(now) == ["周会"]
    assert await watcher.tick(now + timedelta(minutes=1)) == []  # 一个会只提醒一次
    kind, _, text, push = told[0]
    assert kind == "meeting" and "周会" in text and "8 分钟后" in text
    assert "周会" not in push  # 推出去的不带标题
    off = MeetingWatcher(lambda: cal, lambda *a: told.append(a), lambda: False)
    assert await off.tick(now) == []


def test_settings_never_echo_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    applied: list[dict[str, str]] = []
    monkeypatch.setattr(deps, "apply", lambda updates: applied.append(updates) or ["agenda"])
    client = TestClient(app)
    assert client.put("/settings", json={"calendarUrl": "ftp://x"}).status_code == 422
    assert client.put("/settings", json={"mailUser": "not-an-email"}).status_code == 422
    client.put("/settings", json={"calendarUrl": "webcal://cal.example/secret.ics", "mailUser": "me@qq.com", "mailPass": "abcd"})
    assert applied[-1] == {"AILA_CALENDAR_URL": "webcal://cal.example/secret.ics", "AILA_MAIL_USER": "me@qq.com", "AILA_MAIL_PASS": "abcd"}
    monkeypatch.setattr(deps, "settings", deps.settings.__class__(**{**deps.settings.__dict__, "calendar_url": "https://cal.example/secret.ics",
                                                                      "mail_user": "me@qq.com", "mail_pass": "abcd"}))
    got = client.get("/settings").json()
    assert got["agenda"] == {"calendarSet": True, "mailUser": "me@qq.com", "mailPassSet": True, "mailHost": ""}
    assert "secret.ics" not in str(got) and "abcd" not in str(got)
    # 没配的时候：接口说没配，不出错
    monkeypatch.setattr(deps, "settings", deps.settings.__class__(**{**deps.settings.__dict__, "calendar_url": "", "mail_user": "", "mail_pass": ""}))
    assert client.get("/agenda/today").json() == {"configured": False, "events": [], "error": ""}
    assert client.post("/agenda/test-mail").status_code == 409
