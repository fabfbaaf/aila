"""界面语言：英文界面时，把发给桌面端的文字（工具进度、确认、错误）换成英文。

为什么在「出口」翻，而不是改每一处拼字的代码：
- 会话日志、发给大模型的内容（工具结果、提示词）都保持原样：她的上下文和前缀缓存不受语言影响，测试也不用改
- 只在发给桌面端的那一刻（SSE 事件里的进度、确认、错误，HTTP 错误的 detail）查一下词典
- 词典的键是中文原文；f-string 写成 {0} {1} 的模板（「读 {0}」），按整句匹配，文件内容这类东西不会被误翻
- 词典在 locales_en.py，由脚本从代码里找出来（raise 的错误、工具的进度和确认文字），tests/test_i18n.py 检查都有英文
"""

import re
from functools import lru_cache

from .locales_en import EN

HOLE = re.compile(r"\{(\d+)\}")


def current_language() -> str:
    from . import deps  # 用到时再取：设置里换了语言马上生效

    return getattr(deps.settings, "language", "zh")


@lru_cache(maxsize=1)
def _templates() -> list[tuple[re.Pattern[str], str]]:
    """带 {0} 的模板 → 整句匹配的正则；字面部分越长的越靠前（越具体）"""
    out = []
    for zh, en in EN.items():
        if not HOLE.search(zh):
            continue
        parts = HOLE.split(zh)
        regex = "".join(re.escape(p.replace("{{", "{").replace("}}", "}")) if i % 2 == 0 else f"(?P<g{p}>.+?)"
                        for i, p in enumerate(parts))
        out.append((re.compile(regex, re.S), en, len(HOLE.sub("", zh))))
    out.sort(key=lambda x: -x[2])
    return [(r, en) for r, en, _ in out]


def localize(text: str, lang: str | None = None) -> str:
    """中文原文 → 现在的语言（不认识的原样）"""
    if not isinstance(text, str) or not text or (lang or current_language()) != "en":
        return text
    if text in EN:
        return EN[text]
    for regex, en in _templates():
        m = regex.fullmatch(text)
        if m:
            # 填进去的部分正好是词典里的一整条（「工具结果：读文件」里的「读文件」）也换掉；文件名这类原样
            return HOLE.sub(lambda h: EN.get(m.group(f"g{h.group(1)}"), m.group(f"g{h.group(1)}")), en.replace("{{", "\x00").replace("}}", "\x01")).replace("\x00", "{").replace("\x01", "}")
    return text


# 事件里哪些字段是给人看的（别的字段、她说的话、工具结果都不动）
EVENT_FIELDS = {
    "tool_start": ("status",),
    "confirm": ("action", "detail", "reason"),
    "status": ("text",),
    "error": ("message",),
    "plan_pause": ("text",),
    "merge": ("reason",),
    "turn_end": ("error",),
}


# 只有整段正好是词典里的一条才换的字段：工具结果大多是文件内容、命令输出，只有「被打断了」「只读模式不能改」
# 这类代码写的固定结果才翻（不套模板，免得文件里正好有一行「读 xxx」被翻掉）
EXACT_FIELDS = {"tool": ("content",)}
# 工具结果开头固定的一行（后面是清单本身）：只换这一行
RESULT_HEADS = {"待办清单已更新：\n": "To-do list updated:\n"}


def localize_summary(summary: dict) -> dict:
    """会话摘要（列表、看板）里等你同意的那个操作说明按现在的语言（返回新的）"""
    pending = summary.get("pending")
    if not isinstance(pending, dict) or not isinstance(pending.get("action"), str) or current_language() != "en":
        return summary
    return {**summary, "pending": {**pending, "action": localize(pending["action"], "en")}}


def localize_labels(items: list[dict], field: str = "label") -> list[dict]:
    """一组 {label: …} 的显示名换成现在的语言（上下文账本这类代码拼出来的名字），返回新的"""
    return [{**x, field: localize(x[field])} if isinstance(x.get(field), str) else x for x in items]


def localize_event(event: dict) -> dict:
    """SSE 事件发出去之前：英文界面时把给人看的几个字段换成英文（返回新的，不改日志里的那份）"""
    kind = event.get("type", "")
    fields, exact = EVENT_FIELDS.get(kind, ()), EXACT_FIELDS.get(kind, ())
    if not (fields or any(isinstance(event.get(f), str) for f in exact)) or current_language() != "en":
        return event
    out = dict(event)
    for f in fields:
        if isinstance(out.get(f), str):
            out[f] = localize(out[f], "en")
    for f in exact:
        value = out.get(f)
        if not isinstance(value, str):
            continue
        head = next((h for h in RESULT_HEADS if value.startswith(h)), None)
        out[f] = RESULT_HEADS[head] + value[len(head):] if head else EN.get(value, value)
    return out if out != event else event
