"""从大模型的流式输出里拆出表情和动作标签。

标签形如 [happy]，可能被拆在两个数据块里（"[ha" + "ppy]"），所以要缓冲。
只认白名单里的标签，其他方括号原样保留，避免把代码里的 a[i] 吃掉。
"""

from .persona import ACTIONS, ALIASES, EMOTIONS

# 最长的标签（含方括号）不超过这个长度；超过还没闭合，就不是标签
MAX_TAG_LEN = 12

Event = dict[str, str]


class TagParser:
    def __init__(self) -> None:
        self._buf = ""
        self._after_tag = False

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

            event = _tag_event(self._buf[1:end]) if end < MAX_TAG_LEN else None
            if event is None:
                # 不是标签，比如代码里的 a[i]：把 "[" 当普通文字，继续往后找
                text.append("[")
                self._buf = self._buf[1:]
                continue

            _push_text(events, text)
            events.append(event)
            self._buf = self._buf[end + 1 :]
            self._after_tag = True

        _push_text(events, text)
        return events

    def flush(self) -> list[Event]:
        """输出结束：缓冲里剩下的都当普通文字"""
        rest, self._buf = self._buf, ""
        return [{"type": "text", "text": rest}] if rest else []


def _tag_event(raw: str) -> Event | None:
    name = raw.strip().lower()
    name = ALIASES.get(name, name)
    if name in EMOTIONS:
        return {"type": "emotion", "name": name}
    if name in ACTIONS:
        return {"type": "action", "name": name}
    return None


def _push_text(events: list[Event], parts: list[str]) -> None:
    joined = "".join(parts)
    parts.clear()
    if joined:
        events.append({"type": "text", "text": joined})
