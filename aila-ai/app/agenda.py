"""日历：读你的日程（QQ 邮箱、163 这类日历给的订阅链接，ICS 格式），只读。

- 订阅链接存在 .env（AILA_CALENDAR_URL，和大模型密钥一样不回显：链接本身就能看你的日程）
- 每 15 分钟最多取一次，取不到就用上一次的
- 重复的会议（RRULE）用 dateutil 展开（trafilatura 本来就依赖它，见 requirements.txt）；排除 EXDATE，
  改过时间的那一次（RECURRENCE-ID）用改过的；取消的不算
- 时区：带 TZID 的按那个时区换成本地时间，结尾 Z 的是 UTC，什么都没带的当本地时间；全天的事按当天 0 点
- 日程内容只当数据（会议标题、地点里写的话不是给她的指令）

用在：对话里问「今天有什么会」、早上的问候（桌面端 briefing.ts）、会前 10 分钟提醒（桌面上冒泡、手机上推一条）。
"""

import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from dateutil.rrule import rrulestr

FETCH_EVERY = 15 * 60
MAX_BYTES = 5 * 1024 * 1024
TIMEOUT = 15.0
MAX_INSTANCES = 2000


@dataclass(frozen=True)
class Event:
    uid: str
    title: str
    start: datetime  # 本地时间（带时区）
    end: datetime
    all_day: bool = False
    location: str = ""

    def public(self) -> dict[str, Any]:
        return {"uid": self.uid, "title": self.title, "start": self.start.isoformat(), "end": self.end.isoformat(),
                "allDay": self.all_day, "location": self.location}

    def line(self) -> str:
        """给大模型、桌面端看的一行"""
        when = "全天" if self.all_day else f"{self.start:%H:%M}-{self.end:%H:%M}"
        where = f"（{self.location}）" if self.location else ""
        return f"{self.start:%m-%d} {when} {self.title}{where}"


# ---------- 解析 ----------


def _unfold(text: str) -> list[str]:
    """ICS 长行会折成好几行（下一行以空格或 Tab 开头）：先接回来"""
    out: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and out:
            out[-1] += raw[1:]
        elif raw:
            out.append(raw)
    return out


def _unescape(value: str) -> str:
    return re.sub(r"\\([nN\\;,])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), value).strip()


def _prop(line: str) -> tuple[str, dict[str, str], str]:
    """「DTSTART;TZID=Asia/Shanghai:20261125T100000」→（名字，参数，值）"""
    head, _, value = line.partition(":")
    name, *params = head.split(";")
    parsed = {}
    for p in params:
        k, _, v = p.partition("=")
        parsed[k.upper()] = v.strip('"')
    return name.upper(), parsed, value


def _zone(name: str, local: tzinfo) -> tzinfo:
    try:
        return ZoneInfo(name)
    except Exception:  # Outlook 有时写「China Standard Time」这种 Windows 名字：当本地时间
        return local


def _time(value: str, params: dict[str, str], local: tzinfo) -> tuple[datetime, bool]:
    """ICS 的时间 → 本地时间；第二个值是不是全天"""
    value = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        d = datetime.strptime(value[:8], "%Y%m%d")
        return d.replace(tzinfo=local), True
    utc = value.endswith("Z")
    dt = datetime.strptime(value.rstrip("Z")[:15], "%Y%m%dT%H%M%S")
    if utc:
        return dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(local), False
    zone = _zone(params["TZID"], local) if "TZID" in params else local
    return dt.replace(tzinfo=zone).astimezone(local), False


DURATION = re.compile(r"^([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


def _duration(value: str) -> timedelta:
    m = DURATION.match(value.strip())
    if not m:
        return timedelta(hours=1)
    sign, w, d, h, mi, s = m.groups()
    delta = timedelta(weeks=int(w or 0), days=int(d or 0), hours=int(h or 0), minutes=int(mi or 0), seconds=int(s or 0))
    return -delta if sign == "-" else delta


@dataclass
class _Raw:
    uid: str = ""
    title: str = ""
    location: str = ""
    start: datetime | None = None
    end: datetime | None = None
    duration: timedelta | None = None
    all_day: bool = False
    rrule: str = ""
    exdates: list[datetime] | None = None
    recurrence_id: datetime | None = None
    cancelled: bool = False


def parse_ics(text: str, local: tzinfo) -> list[_Raw]:
    events: list[_Raw] = []
    cur: _Raw | None = None
    for line in _unfold(text):
        name, params, value = _prop(line)
        if name == "BEGIN" and value.upper() == "VEVENT":
            cur = _Raw(exdates=[])
        elif name == "END" and value.upper() == "VEVENT":
            if cur is not None and cur.start is not None:
                events.append(cur)
            cur = None
        elif cur is None:
            continue
        elif name == "UID":
            cur.uid = value.strip()
        elif name == "SUMMARY":
            cur.title = _unescape(value)[:200]
        elif name == "LOCATION":
            cur.location = _unescape(value)[:200]
        elif name == "DTSTART":
            cur.start, cur.all_day = _time(value, params, local)
        elif name == "DTEND":
            cur.end, _ = _time(value, params, local)
        elif name == "DURATION":
            cur.duration = _duration(value)
        elif name == "RRULE":
            cur.rrule = value.strip()
        elif name == "EXDATE":
            for v in value.split(","):
                if v.strip():
                    cur.exdates = [*(cur.exdates or []), _time(v, params, local)[0]]
        elif name == "RECURRENCE-ID":
            cur.recurrence_id, _ = _time(value, params, local)
        elif name == "STATUS" and value.strip().upper() == "CANCELLED":
            cur.cancelled = True
    return events


UNTIL = re.compile(r"UNTIL=(\d{8})(T\d{6})?(Z)?")


def _rule(rrule: str, local: tzinfo) -> str:
    """开始时间带时区时，dateutil 要求 UNTIL 是 UTC：没写 Z 的按本地时间换过去（只写日期的算到那天结束）"""
    def fix(m: re.Match[str]) -> str:
        if m.group(3):
            return m.group(0)
        day = datetime.strptime(m.group(1), "%Y%m%d")
        at = datetime.strptime(m.group(1) + m.group(2), "%Y%m%dT%H%M%S") if m.group(2) else day.replace(hour=23, minute=59, second=59)
        utc = at.replace(tzinfo=local).astimezone(ZoneInfo("UTC"))
        return f"UNTIL={utc:%Y%m%dT%H%M%S}Z"

    return UNTIL.sub(fix, rrule)


def expand(raws: list[_Raw], start: datetime, end: datetime) -> list[Event]:
    """[start, end) 里会发生的每一次（重复的展开、排除、改过的用改过的），按开始时间排"""
    overrides = {(r.uid, r.recurrence_id) for r in raws if r.recurrence_id is not None}
    out: list[Event] = []
    for r in raws:
        assert r.start is not None
        length = (r.end - r.start) if r.end else (r.duration or (timedelta(days=1) if r.all_day else timedelta(hours=1)))
        if r.rrule and r.recurrence_id is None:
            try:
                rule = rrulestr(_rule(r.rrule, r.start.tzinfo or start.tzinfo), dtstart=r.start)  # type: ignore[arg-type]
                starts = rule.between(start - length, end, inc=True)[:MAX_INSTANCES]
            except (ValueError, TypeError):
                starts = [r.start]
            skip = {e for e in r.exdates or []}
            starts = [s for s in starts if s not in skip and (r.uid, s) not in overrides]
        else:
            starts = [r.start]
        if r.cancelled:
            continue
        for s in starts:
            e = s + length
            if e > start and s < end:
                out.append(Event(r.uid, r.title or "（没有标题）", s, e, r.all_day, r.location))
    return sorted(out, key=lambda e: e.start)


# ---------- 取 ----------


class Calendar:
    """订阅链接 → 日程（带缓存）"""

    def __init__(self, url: str, client: httpx.AsyncClient, local: tzinfo | None = None) -> None:
        self.url = url.strip().replace("webcal://", "https://", 1)
        self.client = client
        self.local = local or datetime.now().astimezone().tzinfo  # type: ignore[assignment]
        self._raws: list[_Raw] | None = None
        self._at = 0.0
        self.error = ""

    async def _load(self) -> list[_Raw]:
        if self._raws is not None and time.monotonic() - self._at < FETCH_EVERY:
            return self._raws
        try:
            res = await self.client.get(self.url, timeout=TIMEOUT, follow_redirects=True)
            res.raise_for_status()
            if len(res.content) > MAX_BYTES:
                raise ValueError("日历太大了")
            if b"BEGIN:VCALENDAR" not in res.content[:2000]:
                raise ValueError("这个链接不是日历订阅（ICS）")
            self._raws = parse_ics(res.content.decode("utf-8", "replace"), self.local)  # type: ignore[arg-type]
            self._at = time.monotonic()
            self.error = ""
        except (httpx.HTTPError, ValueError) as err:
            self.error = f"日历取不到：{err if isinstance(err, ValueError) else type(err).__name__}"
            if self._raws is None:
                raise CalendarError(self.error) from err
        return self._raws

    async def between(self, start: datetime, end: datetime) -> list[Event]:
        return expand(await self._load(), start, end)

    async def day(self, d: date) -> list[Event]:
        start = datetime(d.year, d.month, d.day, tzinfo=self.local)
        return await self.between(start, start + timedelta(days=1))


class CalendarError(Exception):
    """说给用户听的错误"""
