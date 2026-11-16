"""回想：每次对话前，从记忆里挑出和这次话题相关的几条放进提示词。

不是所有记忆都塞进去：多了浪费 token，还会让她在不相关的时候硬提往事。挑选规则：
- 身份类（profile）最近的几条总是带上：她应该一直知道你是谁
- 两天内要发生的事（event）总是带上：她可以主动关心「明天的面试准备好了吗」
- 其余按和最近几句话的相似度挑：最相似的 3 条（只要不是完全无关）+ 相似度很高的
- 过期的不要（「上周三的面试」已经没用了）
记忆很少时（不超过 8 条）全部带上，不用算向量。

为什么不用一个固定阈值：用 bge-small-zh 实测，「你好」和「用户是后端实习生」的相似度 0.45，
反而比「中午吃什么好」和「用户不吃香菜」的 0.30 还高。绝对分数受句子长短、话题宽窄影响太大，
按相对排名取前几条更稳；多带一两条不太相关的记忆代价很小（提示词里让她不要硬提）。
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from .embedder import Embedder

MAX_RECALL = 8
ALWAYS_PROFILE = 3
UPCOMING = timedelta(days=2)
# 按相似度取前几条；低于 FLOOR 的算完全无关，不要
TOP_RELATED = 3
FLOOR = 0.25
# 高于这个的一定相关，名额够就都带上
STRONG = 0.5


@dataclass(frozen=True)
class Memory:
    id: str
    content: str
    kind: str = "other"
    expires_at: datetime | None = None
    updated_at: datetime | None = None


def active(memories: list[Memory], now: datetime) -> list[Memory]:
    return [m for m in memories if m.expires_at is None or m.expires_at > now]


def recall(memories: list[Memory], query: str, embedder: Embedder | None, now: datetime) -> list[Memory]:
    alive = sorted(active(memories, now), key=lambda m: m.updated_at or now, reverse=True)
    if len(alive) <= MAX_RECALL:
        return alive

    picked: dict[str, Memory] = {}
    for m in [m for m in alive if m.kind == "profile"][:ALWAYS_PROFILE]:
        picked[m.id] = m
    for m in alive:
        if m.kind == "event" and m.expires_at is not None and m.expires_at - now <= UPCOMING:
            picked[m.id] = m

    rest = [m for m in alive if m.id not in picked]
    if embedder is None or not query.strip():
        # 没有向量模型时退而求其次：最近更新的几条
        for m in rest[: MAX_RECALL - len(picked)]:
            picked[m.id] = m
        return list(picked.values())

    scores = embedder.embed([m.content for m in rest]) @ embedder.embed_query(query)
    for rank, i in enumerate(np.argsort(-scores)):
        if len(picked) >= MAX_RECALL or scores[i] < FLOOR:
            break
        if rank >= TOP_RELATED and scores[i] < STRONG:
            break
        picked[rest[i].id] = rest[i]
    return list(picked.values())


def related(memories: list[Memory], text: str, embedder: Embedder | None, k: int) -> list[Memory]:
    """和一段话最相关的 k 条（提取记忆时，用来找可能要修改或删除的旧记忆）"""
    if len(memories) <= k or embedder is None:
        return memories[:k] if embedder is None else memories
    scores = embedder.embed([m.content for m in memories]) @ embedder.embed_query(text)
    return [memories[i] for i in np.argsort(-scores)[:k]]


def describe(memories: list[Memory], now: datetime) -> str:
    """跟着用户最新一句话的背景（放在 <背景> 里，系统提示词保持不变）"""
    weekday = "一二三四五六日"[now.weekday()]
    lines = [f"今天是 {now.year} 年 {now.month} 月 {now.day} 日，星期{weekday}。"]
    if memories:
        lines.append("")
        lines.append("你记得的关于用户的事（来自以前的对话，可能不全）：")
        for m in memories:
            when = f"（到 {m.expires_at.month} 月 {m.expires_at.day} 日）" if m.expires_at else ""
            lines.append(f"- {m.content}{when}")
        lines.append("自然地用上这些，比如称呼、关心近况；不要每次都提，也不要说「根据我的记忆」。")
    return "\n".join(lines)
