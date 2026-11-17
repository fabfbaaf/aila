"""拆出对话回复的表情、动作和口播范围，保留完整的文字。

标签形如 [happy]，可能被拆在两个数据块里（"[ha" + "ppy]"），所以要缓冲。
模型没给口播范围时，逐块找开头适合念的文字，留住可能被拆开的 Markdown、网址。
只认白名单里的标签，其他方括号原样保留，避免把代码里的 a[i] 吃掉。
"""

import re
from dataclasses import dataclass

from .persona import ACTIONS, ALIASES, EMOTIONS

# 最长的标签（含方括号）不超过这个长度；超过还没闭合，就不是标签
MAX_TAG_LEN = 12

Event = dict[str, str | bool]

SENTENCE_ENDS = "。！？.!?\r\n"
DETAIL_PREFIXES = ("```", "http://", "https://", "www.")
LIST_MARKER = re.compile(r"(?:[-*]|[0-9]{1,9}\.)[ \t\r\n]")
PARTIAL_LIST_MARKER = re.compile(r"(?:[-*]|[0-9]{1,9}\.?)")


@dataclass(frozen=True)
class SpokenPrefix:
    sentences: int = 0
    chars: int = 0
    line_start: bool = True
    in_sentence: bool = False


def spoken_prefix(text: str, state: SpokenPrefix, final: bool = False) -> tuple[int, SpokenPrefix, bool]:
    """返回已确认能念的长度、后续计数和是否遇到分界；分块不全时先留着歧义的几字。"""
    sentences, chars, line_start, in_sentence = state.sentences, state.chars, state.line_start, state.in_sentence
    end = len(text)
    cutoff = False
    for i, char in enumerate(text):
        prefix = text[i:i + 8].lower()
        cutoff = (
            any(prefix.startswith(marker) for marker in DETAIL_PREFIXES)
            or (line_start and LIST_MARKER.match(text, i) is not None)
            or (sentences >= 2 and char not in SENTENCE_ENDS and not char.isspace())
            or (chars >= 60 and char.isalnum())
        )
        if cutoff:
            end = i
            break
        if not final and len(text) - i <= 10:
            rest = text[i:]
            if any(marker.startswith(prefix) for marker in DETAIL_PREFIXES) or (
                line_start and PARTIAL_LIST_MARKER.fullmatch(rest) is not None
            ):
                end = i
                break
        if char.isalnum():
            chars += 1
            in_sentence = True
        if char in SENTENCE_ENDS and in_sentence:
            sentences += 1
            in_sentence = False
        line_start = char in "\r\n" or (line_start and char in " \t")
    return end, SpokenPrefix(sentences, chars, line_start, in_sentence), cutoff


class TagParser:
    def __init__(self) -> None:
        self._buf = ""
        self._after_tag = False
        self._used_say = False
        self._saying = False
        self._segment_started = False
        self._fallback = SpokenPrefix()
        self._pending = ""
        self._cutoff = False
        self._omitted = False

    def feed(self, chunk: str) -> list[Event]:
        """喂入一块文本，返回这块里已经能确定的事件（文本、表情、动作）"""
        self._buf += chunk
        events: list[Event] = []
        text: list[str] = []

        while self._buf:
            if self._after_tag:
                # 标签后面的空格没有意义，去掉
                self._buf = self._buf.lstrip(" ")
                if not self._buf:
                    break
                self._after_tag = False

            start = self._buf.find("[")
            if start < 0:
                text.append(self._buf)
                self._buf = ""
                break

            text.append(self._buf[:start])
            self._buf = self._buf[start:]
            end = self._buf.find("]")

            if end < 0:
                if len(self._buf) < MAX_TAG_LEN:
                    break  # 可能是被拆开的标签，等下一块
                text.append("[")
                self._buf = self._buf[1:]
                continue

            name = self._buf[1:end].strip().lower() if end < MAX_TAG_LEN else ""
            event = _tag_event(name)
            if event is None and name not in ("say", "/say"):
                # 不是标签，比如代码里的 a[i]：把 "[" 当普通文字，继续往后找
                text.append("[")
                self._buf = self._buf[1:]
                continue

            self._push_text(events, "".join(text), final=True)
            text.clear()
            if name == "say":
                if not self._used_say and not self._segment_started:
                    self._used_say = self._saying = True
            elif name == "/say":
                self._saying = False
            elif event is not None:
                events.append(event)
                self._after_tag = True
            self._buf = self._buf[end + 1 :]

        self._push_text(events, "".join(text))
        return events

    def flush(self, *, final: bool = True) -> list[Event]:
        """模型一轮结束，交出残留文字；整次回复结束时才提示还有全文。"""
        events: list[Event] = []
        rest, self._buf = self._buf, ""
        self._push_text(events, rest, final=True)
        self._segment_started = self._after_tag = self._saying = False
        if final and self._omitted and not self._used_say:
            events.append({"type": "text", "text": "", "spoken": True, "hint": "more"})
            self._omitted = False
        return events

    def _push_text(self, events: list[Event], text: str, final: bool = False) -> None:
        text, self._pending = self._pending + text, ""
        if not text:
            return
        self._segment_started |= bool(text.strip())
        if self._used_say:
            events.append({"type": "text", "text": text, "spoken": self._saying})
        elif self._cutoff:
            self._silent(events, text)
        else:
            end, self._fallback, self._cutoff = spoken_prefix(text, self._fallback, final)
            if end:
                events.append({"type": "text", "text": text[:end], "spoken": True})
            if self._cutoff:
                self._silent(events, text[end:])
            else:
                self._pending = text[end:]

    def _silent(self, events: list[Event], text: str) -> None:
        if text:
            self._omitted |= bool(text.strip())
            events.append({"type": "text", "text": text, "spoken": False})


def _tag_event(name: str) -> Event | None:
    name = ALIASES.get(name, name)
    if name in EMOTIONS:
        return {"type": "emotion", "name": name}
    if name in ACTIONS:
        return {"type": "action", "name": name}
    return None
