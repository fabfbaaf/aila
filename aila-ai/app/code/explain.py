"""看懂项目的讲解：每一层（整体架构、模块、链路、文件、函数）、还有「出问题了」，把代码算出来的事实和一小段源码
交给大模型，让它用大白话讲。

为什么这样设计：
- 图和事实是代码从项目里算的（understand.py、代码索引），一定是真的；讲解是 AI 说的。界面上两样分开显示，
  提示词里也只许它根据给它的内容讲，看不出来就直说，不编函数名、不编文件
- 两种讲法：零基础（少用术语，用到就顺手解释，多打比方）和开发者（职责、设计取舍、要注意的地方）
- 按内容缓存（数据目录/understand-cache.json）：同一段代码、同一种讲法不重复花钱；代码改了内容就变了，自然重新讲
- 没填大模型密钥（假模型）时不讲，只给事实
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger("aila")

DEPTHS = {
    "beginner": "对方刚开始学编程：少用术语，用到就顺手解释一句；多打比方；一步一步说",
    "dev": "对方是开发者：直接说职责、数据怎么流、设计上的取舍、改它要注意什么",
}
LEVELS = {
    "area": "项目或其中一个目录的整体结构",
    "chain": "一条从界面（或某个接口）一路连到最终执行代码的链路",
    "file": "一个文件",
    "function": "一个函数",
    "diagnose": "一个报错：为什么会出这个错",
}
SYSTEM = """你是艾拉，在给用户讲解一个代码项目。下面给你的「事实」是程序从代码里算出来的，「代码」是项目里的原文。
只根据这些内容讲：看不出来的就说看不出来，不要编造函数名、文件名或者没给你的细节。
讲法：{depth}。中文，口语，简短，不说「您」，不用套话。
要讲的是：{what}。
只输出一个 JSON 对象：{{"summary": "一句话说清它是干什么的", "detail": "两到五句展开讲", "watch": "要注意的地方或者常见坑，没有就是空字符串"}}"""
DIAGNOSE_SYSTEM = """你是艾拉，在帮用户弄明白一个报错。下面是报错原文、程序从代码里找出的出错位置和调用链，以及相关函数的原文。
只根据这些内容推断：说清楚「哪一步出了什么问题、为什么」，再说怎么修；看不准的地方直说是猜测，不要编造没给你的代码。
讲法：{depth}。中文，口语，简短，不说「您」。
只输出一个 JSON 对象：{{"summary": "一句话：错在哪、为什么", "detail": "两到六句：顺着调用链讲清楚怎么走到这一步的", "watch": "怎么修（一两句），看不出来就是空字符串"}}"""

MAX_FACTS = 6000
MAX_CODE = 9000
MAX_CACHE = 600


class ExplainCache:
    """讲解的缓存：键是（层级、讲法、事实、代码）的哈希。最多存 MAX_CACHE 条，多了丢最早的"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._items: dict[str, dict[str, str]] = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            self._items = {}

    @staticmethod
    def key(*parts: str) -> str:
        return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:32]

    def get(self, key: str) -> dict[str, str] | None:
        with self._lock:
            return self._items.get(key)

    def put(self, key: str, value: dict[str, str]) -> None:
        with self._lock:
            self._items.pop(key, None)
            self._items[key] = value
            while len(self._items) > MAX_CACHE:
                self._items.pop(next(iter(self._items)))
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(self._items, ensure_ascii=False), encoding="utf-8")
            except OSError as err:
                log.warning("讲解缓存存不下来：%s", err)


def parse_answer(raw: str) -> dict[str, str] | None:
    """模型回的 JSON（可能包在代码块里、前后有废话）→ summary / detail / watch；不像样就是 None"""
    text = raw.strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict) or not str(data.get("summary", "")).strip():
        return None
    return {k: str(data.get(k, "") or "").strip()[:2000] for k in ("summary", "detail", "watch")}


async def explain(model: Any, cache: ExplainCache, level: str, depth: str, facts: str, code: str = "") -> dict[str, Any]:
    """讲一层。model 是 None（没填密钥）就只回事实；出错了也只回事实，带上原因"""
    depth = depth if depth in DEPTHS else "beginner"
    facts, code = facts[:MAX_FACTS], code[:MAX_CODE]
    out: dict[str, Any] = {"facts": facts, "explanation": None, "cached": False, "note": ""}
    if model is None:
        out["note"] = "还没填大模型密钥（设置 → 对话），只显示代码算出来的事实。"
        return out
    key = ExplainCache.key(level, depth, facts, code)
    hit = cache.get(key)
    if hit:
        return {**out, "explanation": hit, "cached": True}
    system = (DIAGNOSE_SYSTEM if level == "diagnose" else SYSTEM).format(depth=DEPTHS[depth], what=LEVELS.get(level, level))
    user = f"【事实】\n{facts}" + (f"\n\n【代码】\n{code}" if code else "")
    try:
        raw = await model.complete([{"role": "system", "content": system}, {"role": "user", "content": user}])
    except Exception as err:  # 网络、额度、密钥：讲不了就只给事实
        log.info("讲解没成：%s", err)
        out["note"] = f"这次没讲成：{str(err)[:160]}"
        return out
    answer = parse_answer(raw)
    if answer is None:
        out["note"] = "模型回的不像样，这次只显示事实。"
        return out
    cache.put(key, answer)
    return {**out, "explanation": answer}


# ---------- 每一层的事实（给模型看，也原样显示在界面上） ----------


def area_facts(view: dict[str, Any], link_text: Any, readme: str = "") -> str:
    lines = [f"位置：{view['path'] or '项目根目录'}"]
    for c in view["children"]:
        kind = "目录" if c["kind"] == "dir" else "文件"
        langs = "、".join(c["langs"]) or "未知语言"
        test = "（全是测试）" if c.get("onlyTests") else ""
        lines.append(f"- {kind} {c['name']}：{c['files']} 个文件、{c['lines']} 行、{c['functions']} 个函数，{langs}{test}")
    if view["hidden"]:
        lines.append(f"- 还有 {view['hidden']} 个小的没列出来")
    if view["links"]:
        lines.append("它们之间的连线：")
        lines += [f"- {link_text(link)}" for link in view["links"][:30]]
    if view["entries"]:
        lines.append("入口文件：" + "、".join(view["entries"]))
    if view["hubs"]:
        lines.append("被用得最多的文件：" + "、".join(view["hubs"]))
    if readme:
        lines.append(f"这个目录的 README 开头：\n{readme[:1500]}")
    return "\n".join(lines)


def chain_facts(view: dict[str, Any]) -> str:
    lines = [f"起点：{view['start']}"]
    by_layer: dict[int, list[str]] = {}
    for n in view["nodes"]:
        by_layer.setdefault(int(n["layer"]), []).append(str(n["id"]))
    for layer in sorted(by_layer):
        where = "起点" if layer == 0 else (f"往下第 {layer} 步" if layer > 0 else f"往上第 {-layer} 步（用到它的）")
        lines.append(f"{where}：" + "、".join(by_layer[layer]))
    kinds = {"call": "调用", "import": "引用", "http": "HTTP 接口", "ipc": "IPC 消息"}
    lines.append("连线：")
    for link in view["links"][:40]:
        via = f"（{'、'.join(link['via'][:3])}）" if link.get("via") else ""
        lines.append(f"- {link['source']} → {link['target']}：{kinds.get(link['kind'], link['kind'])}{via}")
    return "\n".join(lines)


def file_facts(file: str, outline: list[Any], uses: list[str], used_by: list[str]) -> str:
    lines = [f"文件：{file}"]
    if outline:
        lines.append("里面的函数和类：")
        for c in outline[:40]:
            doc = f" —— {c.doc.splitlines()[0][:100]}" if c.doc else ""
            lines.append(f"- {c.kind} {c.qname}（第 {c.start}-{c.end} 行）：{c.signature[:160]}{doc}")
    if uses:
        lines.append("它用到的文件：" + "、".join(uses[:15]))
    if used_by:
        lines.append("用到它的文件：" + "、".join(used_by[:15]))
    return "\n".join(lines)


def function_facts(chunk: Any, callers: list[Any], callees: list[Any]) -> str:
    lines = [f"函数：{chunk.qname}（{chunk.file} 第 {chunk.start}-{chunk.end} 行）", f"签名：{chunk.signature[:300]}"]
    if chunk.doc:
        lines.append(f"注释：{chunk.doc[:600]}")
    if callers:
        lines.append("谁调用它：" + "、".join(f"{c.qname}（{c.file}）" for c in callers))
    if callees:
        lines.append("它调用了：" + "、".join(f"{c.qname}（{c.file}）" for c in callees))
    return "\n".join(lines)


def numbered(lines: list[str], start: int) -> str:
    return "\n".join(f"{start + i:>5}  {line}" for i, line in enumerate(lines))


__all__ = ["DEPTHS", "ExplainCache", "area_facts", "chain_facts", "explain", "file_facts", "function_facts", "numbered",
           "parse_answer"]
