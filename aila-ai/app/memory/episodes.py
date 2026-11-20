"""经历：对话太长时滑出窗口的那段的摘要、编程任务每一轮做了什么；以及对话和编程任务开头从记忆库里回想什么。

为什么这样设计：
- 短期记忆是对话窗口（最近 40 句）。以前滑出去的就没了，现在滑出去的那段先让大模型写成一小段摘要存进记忆库：
  最新的一份每次都带上（「刚才说到哪了」），更早的按话题相关度找
- 摘要按「哪几句已经摘要过」记（每句话一个指纹），不按位置：桌面端的历史怎么截、怎么滑都不会重复摘要
- 摘要在后台做，失败了不记指纹，下次滑出时连同新的一起再摘要（自然重试）
- 编程任务的经历不用大模型：从这一轮的事件里拼出来（你让她做什么、改了哪些文件、最后怎么说的、怎么结束的），
  不花钱、不会编
- 编程会话开头带上关于用户的几条（身份、喜好）和这个项目最近几次做了什么。只在开头放一次（写进 start 事件），
  会话中途不改，前缀缓存照样命中
- 摘要里不留密码、验证码、证件号这类（和整理记忆的规则一样）
"""

import asyncio
import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .extract import SENSITIVE, _strip_tags
from .recall import Memory, active, describe, recall
from .store import EPISODE, PROJECT, MemoryStore, message_hash

log = logging.getLogger("aila")

DIGEST_CHARS = 400
TASK_CHARS = 300
# 和话题相关的经历最多带几条；相似度低于这个的不要（经历比事实长，相似度普遍偏高，门槛高一点）
RELATED_EPISODES = 3
EPISODE_FLOOR = 0.5
# 编程会话开头：关于你的几条、这个项目最近几次
CODING_FACTS = 6
CODING_EPISODES = 3

DIGEST_SYSTEM = """你在帮「艾拉」记住她和用户以前聊过的内容。下面是一段已经比较早的对话，写一段摘要留着以后回想。

写清楚：聊了哪些话题、用户提到的事情和想法、商量好的结论或约定、还没聊完的事。
用第三人称（「用户」「艾拉」），一段话，150 字以内，不要分点。
不要写密码、验证码、证件号、银行卡这类敏感信息；不要编对话里没有的内容。

只输出 JSON：{"summary": "……"}"""


@dataclass(frozen=True)
class Line:
    role: str
    content: str


# ---------- 对话：滑出窗口的那段 ----------


def fingerprints(lines: Sequence[Line]) -> list[str]:
    return [message_hash(m.role, m.content, lines[i - 1].content if i else "") for i, m in enumerate(lines)]


def undigested(store: MemoryStore, dropped: Sequence[Line]) -> list[tuple[str, Line]]:
    """滑出窗口的这些里，还没摘要过的（按指纹认）"""
    hashes = fingerprints(dropped)
    done = store.digested(hashes)
    return [(h, m) for h, m in zip(hashes, dropped) if h not in done]


def clean_summary(text: str) -> str:
    """一句一句看，像是有敏感信息的整句去掉；限长"""
    one = " ".join(_strip_tags(text).split())
    sentences = re.split(r"(?<=[。！？!?；;])", one)
    kept = "".join(s for s in sentences if s and not SENSITIVE.search(s))
    return kept[:DIGEST_CHARS].strip()


def heuristic_summary(lines: Sequence[Line]) -> str:
    """没接大模型（假模型）时：用户说过的几句话拼起来"""
    said = [" ".join(_strip_tags(m.content).split())[:40] for m in lines if m.role == "user"]
    return ("用户聊到：" + "；".join(s for s in said if s)[:300]) if said else ""


async def digest(model: Any, store: MemoryStore, pending: Sequence[tuple[str, Line]]) -> str:
    """把还没摘要过的那段写成摘要存进记忆库；返回摘要（没写成返回空字符串，指纹不记，下次再来）"""
    if not pending:
        return ""
    lines = [m for _, m in pending]
    if hasattr(model, "complete"):
        convo = "\n".join(f"{'用户' if m.role == 'user' else '艾拉'}：{_strip_tags(m.content)[:600]}" for m in lines)
        raw = await model.complete([{"role": "system", "content": DIGEST_SYSTEM}, {"role": "user", "content": convo[-12000:]}])
        summary = _parse_summary(raw)
    else:
        summary = heuristic_summary(lines)
    summary = clean_summary(summary)
    if not summary:
        return ""
    await asyncio.to_thread(store.add_episode, "", summary,
                            {"source": "chat", "messages": len(lines), "at": datetime.now().astimezone().isoformat()})
    await asyncio.to_thread(store.mark_digested, [h for h, _ in pending])
    return summary


def _parse_summary(raw: str) -> str:
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return ""
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return ""
    value = data.get("summary") if isinstance(data, dict) else None
    return value if isinstance(value, str) else ""


class Digester:
    """后台摘要：同一时间只做一份，任务留着引用不被回收"""

    def __init__(self) -> None:
        self._running: asyncio.Task[str] | None = None

    def schedule(self, model: Any, store: MemoryStore, dropped: Sequence[Line]) -> asyncio.Task[str] | None:
        if self._running is not None and not self._running.done():
            return None  # 正在做，滑出去的下次再算
        pending = undigested(store, dropped)
        if not pending:
            return None
        self._running = asyncio.create_task(self._run(model, store, pending))
        return self._running

    @staticmethod
    async def _run(model: Any, store: MemoryStore, pending: list[tuple[str, Line]]) -> str:
        try:
            return await digest(model, store, pending)
        except Exception as err:  # 后台的事：失败了不记指纹，下次再摘要
            log.warning("对话摘要失败：%s", err)
            return ""


# ---------- 对话开头的背景 ----------


def chat_context(store: MemoryStore | None, memories: list[Memory], query: str, embedder: Any, now: datetime) -> str:
    """日期 + 和最近几句相关的事实 + 最新一份对话摘要 + 相关的经历。在线程里跑（要算向量、查库）"""
    if store is None:
        return describe(recall(memories, query, embedder, now), now)
    store.sync_facts(memories)
    picked = recall(memories, query, embedder, now, store)
    latest = store.recent(EPISODE, "", 1, source="chat")
    earlier = latest[0].content if latest else ""
    skip = {latest[0].id} if latest else set()
    episodes = []
    if query.strip():
        for hit in store.search(query, (EPISODE, PROJECT), k=8):
            if hit.entry.id in skip:
                continue
            ok = hit.similarity >= EPISODE_FLOOR if hit.similarity is not None else hit.keyword
            if ok:
                episodes.append(_label(hit.entry))
            if len(episodes) >= RELATED_EPISODES:
                break
    return describe(picked, now, earlier, episodes)


def _label(entry: Any) -> str:
    if entry.kind == PROJECT:
        return f"（项目约定）{entry.content}"
    if entry.meta.get("source") == "task":
        return f"（编程任务）{entry.content}"
    return entry.content


# ---------- 编程任务 ----------


def task_episode(events: Sequence[dict[str, Any]]) -> str:
    """一轮编程任务的经历：从最后一条 user 事件到 turn_end。没做什么（没有回答也没改文件）就返回空字符串"""
    start = max((i for i, e in enumerate(events) if e.get("type") == "user"), default=-1)
    if start < 0:
        return ""
    turn = events[start:]
    # 出方案、按计划做、审查这几轮的话是代码拼的一长段，用它的短标题（「第 1/2 阶段：读一遍项目」）
    ask = " ".join(str(turn[0].get("title") or turn[0].get("text", "")).split())
    files: list[str] = []
    answer = ""
    reason = ""
    for e in turn:
        t = e.get("type")
        if (t == "tool" and e.get("status") == "ok" and e.get("name") in ("edit_file", "write_file")
                and isinstance(e.get("file"), str)):
            if e["file"] not in files:
                files.append(e["file"])
        elif t == "assistant" and str(e.get("text", "")).strip():
            answer = " ".join(str(e["text"]).split())
        elif t == "turn_end":
            reason = str(e.get("reason", ""))
    if not files and not answer:
        return ""
    if not ask or ask.startswith("/"):
        ask = ask or "接着做"
    parts = [f"让她做：{ask[:80]}"]
    if files:
        more = f" 等 {len(files)} 个文件" if len(files) > 5 else ""
        parts.append("改了 " + "、".join(files[:5]) + more)
    if answer:
        parts.append(f"最后说：{answer[:120]}")
    if reason and reason != "done":
        parts.append(f"（没做完就停了：{reason}）")
    text = "；".join(parts)
    return text if not SENSITIVE.search(text) else clean_summary(text)


def coding_context(store: MemoryStore | None, scope: str, now: datetime) -> str:
    """编程会话开头的一段：关于你的几条 + 这个项目最近几次做了什么。没有就是空字符串"""
    if store is None:
        return ""
    facts = [m for m in active(store.facts(), now) if m.kind in ("profile", "preference")][:CODING_FACTS]
    recent = store.recent(EPISODE, scope, CODING_EPISODES, source="task")
    if not facts and not recent:
        return ""
    lines = ["", "<回想 说明=\"以前记下的，当参考；和代码、和你现在说的对不上时以现在为准\">"]
    if facts:
        lines.append("关于用户：")
        lines.extend(f"- {m.content}" for m in facts)
    if recent:
        lines.append("这个项目最近几次：")
        for e in reversed(recent):
            when = datetime.fromtimestamp(e.updated_at).strftime("%m-%d %H:%M")
            lines.append(f"- {when} {e.content}")
    lines.append("</回想>")
    return "\n".join(lines)


__all__ = ["Digester", "Line", "chat_context", "coding_context", "digest", "fingerprints", "task_episode", "undigested"]
