"""通知：编程任务等你同意、做完、出错、停下时告诉你——手机页面开着时弹一条（app/phone），
设置里填了推送地址时再推到手机的通知栏（ntfy、Bark、企业微信机器人、飞书机器人，自己选）。

为什么推送这样做：局域网里是 http，浏览器不让 http 页面在后台收推送（要 https 和 Service Worker），
所以页面关着时只能借一个推送服务。推出去的只有任务标题和「等你同意 / 做完了」这类状态，不带命令、代码、diff；
手机页面（局域网）上的提醒可以带要做的操作说明（那是代码生成的）。

- 只在打开了「允许手机连接」时通知
- 同一件事 10 秒内不重复推
- 推送地址只存在本机（data/phone.json），设置页不回显
"""

import asyncio
import ipaddress
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

log = logging.getLogger("aila.notify")

PUSH_KINDS = ("none", "ntfy", "bark", "wecom", "feishu")
DEDUPE_SECONDS = 10.0
QUEUE_MAX = 50
END_TEXT = {
    "done": "做完了",
    "error": "出错了",
    "max_steps": "步数用完了，停下了",
    "denied": "被权限挡住了，停下了",
    "budget": "花费到上限了，停下了",
    "timeout": "一直没等到你同意，停下了",
}


@dataclass(frozen=True)
class Notice:
    kind: str  # confirm / end
    sid: str
    title: str
    text: str  # 给手机页面看的（可以带操作说明）
    push: str  # 推出去的（只有状态）
    t: float


def check_push_url(url: str) -> str | None:
    """推送地址：https；或者局域网里自己架的 ntfy 这类（内网地址可以 http）"""
    parts = urlsplit(url.strip())
    if parts.scheme == "https" and parts.hostname:
        return None
    if parts.scheme == "http" and parts.hostname:
        try:
            if ipaddress.ip_address(parts.hostname).is_private:
                return None
        except ValueError:
            pass
    return "推送地址要用 https（局域网里自己架的服务可以 http）。"


def push_request(kind: str, url: str, title: str, text: str) -> dict[str, Any]:
    """各家推送服务的请求格式（纯计算，能单测）"""
    line = f"{title}：{text}" if title else text
    if kind == "ntfy":
        return {"url": url, "content": line.encode("utf-8"), "headers": {"Content-Type": "text/plain; charset=utf-8"}}
    if kind == "bark":
        return {"url": url, "json": {"title": "艾拉", "body": line, "group": "aila"}}
    if kind == "wecom":
        return {"url": url, "json": {"msgtype": "text", "text": {"content": f"艾拉：{line}"}}}
    if kind == "feishu":
        return {"url": url, "json": {"msg_type": "text", "content": {"text": f"艾拉：{line}"}}}
    raise ValueError(kind)


async def send_push(client: httpx.AsyncClient, kind: str, url: str, title: str, text: str) -> None:
    req = push_request(kind, url, title, text)
    res = await client.post(req.pop("url"), timeout=10.0, **req)
    res.raise_for_status()


class Notifier:
    """看编程任务的事件，该告诉你的时候告诉你"""

    def __init__(self, enabled: Callable[[], bool], push: Callable[[], tuple[str, str]], title_of: Callable[[str], str],
                 sender: Callable[[str, str, str, str], Awaitable[None]]) -> None:
        self.enabled = enabled
        self.push = push  # （哪家，地址）
        self.title_of = title_of
        self.sender = sender
        self._queues: set[asyncio.Queue[Notice]] = set()
        self._recent: dict[tuple[str, str, str], float] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def watch(self, sid: str, event: dict[str, Any]) -> None:
        """Runtime 的事件观察者"""
        if not self.enabled():
            return
        kind = event.get("type")
        if kind == "confirm":
            action = str(event.get("action", ""))[:120]
            self.notice("confirm", sid, f"等你同意：{action}" if action else "等你同意一个操作", "等你同意一个操作")
        elif kind == "turn_end":
            text = END_TEXT.get(str(event.get("reason", "")))
            if text:  # 你自己打断的、拒绝的不用告诉你
                self.notice("end", sid, text, text)

    def notice(self, kind: str, sid: str, text: str, push: str) -> None:
        now = time.monotonic()
        key = (kind, sid, text)
        if now - self._recent.get(key, -1e9) < DEDUPE_SECONDS:
            return
        self._recent = {k: v for k, v in self._recent.items() if now - v < DEDUPE_SECONDS}
        self._recent[key] = now
        try:
            title = self.title_of(sid)[:60]
        except Exception:
            title = ""
        n = Notice(kind, sid, title, text, push, time.time())
        for q in list(self._queues):
            if q.qsize() < QUEUE_MAX:
                q.put_nowait(n)
        service, url = self.push()
        if service != "none" and url:
            task = asyncio.get_running_loop().create_task(self._send(service, url, title, push))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _send(self, service: str, url: str, title: str, text: str) -> None:
        try:
            await self.sender(service, url, title, text)
        except Exception as err:  # 推送失败不影响任务；记一笔
            log.warning("推送失败（%s）：%s", service, type(err).__name__)

    async def follow(self) -> AsyncIterator[dict[str, Any]]:
        """手机页面开着时收提醒（SSE）"""
        q: asyncio.Queue[Notice] = asyncio.Queue()
        self._queues.add(q)
        try:
            while True:
                yield {"type": "notice", **asdict(await q.get())}
        finally:
            self._queues.discard(q)
