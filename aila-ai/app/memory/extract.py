"""从对话里提取记忆：每轮对话结束后，让大模型对照已有的相关记忆，决定新增、修改还是删除。

为什么是「操作」而不是「重新生成全部记忆」：只看相关的几条，提示词短、便宜；
冲突时（「搬到上海了」vs「住在北京」）明确地 update 那一条，而不是新旧两条并存让她混乱。

大模型的输出不全信：编号对不上的、内容太长的、像是敏感信息的、和已有记忆几乎一样的，都丢掉。
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any

import numpy as np

from .embedder import Embedder
from .recall import Memory, related

KINDS = ("profile", "preference", "event", "other")
MAX_CONTENT = 120
CANDIDATES = 8
# 和已有记忆相似度超过这个就算重复（同一件事换了个说法）
DUPLICATE = 0.93
# 密码、验证码、证件号、银行卡、手机号：一律不记
SENSITIVE = re.compile(r"密码|口令|password|验证码|身份证|银行卡|卡号|信用卡|\d{11,}", re.IGNORECASE)

SYSTEM = """你在帮「艾拉」整理她对用户的长期记忆。根据最近的对话，决定记忆要怎么更新。

只记对以后聊天有用的、关于用户本人的信息：身份和经历、喜好和习惯、重要的人和宠物、计划和约定、用户明确要求记住的事。
不要记：一次性的问题、闲聊内容、艾拉说的话、网页或资料里的内容、密码证件号银行卡等敏感信息、对用户的猜测。

每条记忆一句话，以「用户」开头，30 字左右，最多 60 字。
kind 只能是 profile（身份、经历）、preference（喜好、习惯）、event（有具体时间的事）、other。
event 要写 expires：过了这一天就不用再记的日期（YYYY-MM-DD）；其他类型 expires 写 null。

和已有记忆重复的不要再加；和已有记忆冲突时用 update 改成新的（比如搬家、换工作）；用户要求忘掉时用 delete。
没有要改的就输出 {"ops": []}。

只输出 JSON，格式：
{"ops": [{"op": "add", "content": "用户……", "kind": "preference", "expires": null},
         {"op": "update", "id": "m1", "content": "用户……"},
         {"op": "delete", "id": "m2"}]}"""


@dataclass(frozen=True)
class Turn:
    role: str
    content: str


async def extract(model: Any, turns: list[Turn], memories: list[Memory], embedder: Embedder | None, now: datetime) -> list[dict[str, Any]]:
    """返回要执行的操作；id 都是真实的记忆 id"""
    user_text = "\n".join(t.content for t in turns if t.role == "user")
    if not user_text.strip():
        return []
    candidates = related(memories, user_text, embedder, CANDIDATES)
    numbered = {f"m{i + 1}": m for i, m in enumerate(candidates)}

    if hasattr(model, "complete"):
        raw = await model.complete(
            [{"role": "system", "content": SYSTEM}, {"role": "user", "content": _prompt(turns, numbered, now)}]
        )
        proposed = _parse(raw)
    else:
        proposed = heuristic(turns, numbered)  # 没接大模型时（假模型）用简单规则，整条链路照样能跑
    return _validate(proposed, numbered, memories, embedder, now)


def _prompt(turns: list[Turn], numbered: dict[str, Memory], now: datetime) -> str:
    weekday = "一二三四五六日"[now.weekday()]
    existing = "\n".join(f"{k}: {m.content}" for k, m in numbered.items()) or "（还没有）"
    convo = "\n".join(f"{'用户' if t.role == 'user' else '艾拉'}：{_strip_tags(t.content)}" for t in turns)
    return f"今天是 {now:%Y-%m-%d}（星期{weekday}）。\n\n已有的相关记忆：\n{existing}\n\n最近的对话：\n{convo}"


def _parse(raw: str) -> list[dict[str, Any]]:
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return []
    ops = data.get("ops") if isinstance(data, dict) else None
    return [o for o in ops if isinstance(o, dict)] if isinstance(ops, list) else []


def _validate(
    proposed: list[dict[str, Any]],
    numbered: dict[str, Memory],
    memories: list[Memory],
    embedder: Embedder | None,
    now: datetime,
) -> list[dict[str, Any]]:
    ops: list[dict[str, Any]] = []
    touched: set[str] = set()
    existing_texts = {m.content for m in memories}
    for p in proposed:
        op = p.get("op")
        if op == "delete":
            m = numbered.get(str(p.get("id")))
            if m and m.id not in touched:
                touched.add(m.id)
                ops.append({"op": "delete", "id": m.id})
            continue

        content = _clean(p.get("content"))
        if content is None:
            continue
        kind = p.get("kind") if p.get("kind") in KINDS else None
        expires = _expires(p.get("expires"), now)
        if op == "update":
            m = numbered.get(str(p.get("id")))
            if m and m.id not in touched and content != m.content:
                touched.add(m.id)
                ops.append({"op": "update", "id": m.id, "content": content, "kind": kind or m.kind,
                            "expiresAt": expires.isoformat() if expires else None})
        elif op == "add":
            if content in existing_texts or _duplicate(content, memories, embedder):
                continue
            existing_texts.add(content)
            kind = kind or "other"
            ops.append({"op": "add", "content": content, "kind": kind,
                        "expiresAt": expires.isoformat() if expires and kind == "event" else None})
    return ops


def _clean(content: Any) -> str | None:
    if not isinstance(content, str):
        return None
    text = " ".join(content.split()).strip("。 ")
    if len(text) < 4 or len(text) > MAX_CONTENT or SENSITIVE.search(text):
        return None
    return text


def _expires(value: Any, now: datetime) -> datetime | None:
    """「2026-11-18」→ 那天结束的时刻（当地时间）；已经过去的日期不要"""
    if not isinstance(value, str):
        return None
    try:
        day = datetime.strptime(value.strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    end = datetime.combine(day, time(23, 59, 59), tzinfo=now.tzinfo)
    return end if end > now else None


def _duplicate(content: str, memories: list[Memory], embedder: Embedder | None) -> bool:
    if embedder is None or not memories:
        return False
    scores = embedder.embed([m.content for m in memories]) @ embedder.embed_query(content)
    return bool(np.max(scores) >= DUPLICATE)


def _strip_tags(text: str) -> str:
    return re.sub(r"\[[a-z]+\]", "", text)


# ---------- 没有大模型时的简单规则 ----------

_FORGET = re.compile(r"忘掉|忘记|别记|不要记")
_SELF = re.compile(r"^(?:记住|请记住|你要记得)?[，,\s]*(我(?:叫|是|喜欢|不喜欢|讨厌|在|住在|养了|有)[^，。！？!?]{1,30})")


def heuristic(turns: list[Turn], numbered: dict[str, Memory]) -> list[dict[str, Any]]:
    ops: list[dict[str, Any]] = []
    for t in turns:
        if t.role != "user":
            continue
        for clause in re.split(r"[。！？!?\n，,；;]", t.content):
            clause = clause.strip()
            if not clause:
                continue
            if _FORGET.search(clause):
                target = _best_overlap(clause, numbered)
                if target:
                    ops.append({"op": "delete", "id": target})
                continue
            m = _SELF.search(clause)
            if m:
                kind = "preference" if re.search("喜欢|讨厌", m.group(1)) else "profile"
                ops.append({"op": "add", "content": "用户" + m.group(1)[1:], "kind": kind, "expires": None})
    return ops


def _best_overlap(text: str, numbered: dict[str, Memory]) -> str | None:
    best, best_score = None, 0.0
    chars = set(text)
    for key, m in numbered.items():
        score = len(chars & set(m.content.removeprefix("用户"))) / max(1, len(set(m.content)))
        if score > best_score:
            best, best_score = key, score
    return best if best_score >= 0.3 else None
