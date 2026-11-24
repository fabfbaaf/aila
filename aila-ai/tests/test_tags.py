"""逐块解析对话标签，验证完整文字和口播范围不受模型分块影响。"""

import pytest

from app.tags import Event, TagParser


def run(*chunks: str) -> list[Event]:
    """把几块文本依次喂进去，相邻的文字事件合并，方便断言"""
    parser = TagParser()
    events = [e for c in chunks for e in parser.feed(c)] + parser.flush()
    merged: list[Event] = []
    for e in events:
        if (
            e["type"] == "text" and "hint" not in e and merged and merged[-1]["type"] == "text"
            and merged[-1].get("spoken") == e.get("spoken")
        ):
            merged[-1]["text"] = f"{merged[-1]['text']}{e['text']}"
        else:
            merged.append(e)
    return merged


def test_plain_text() -> None:
    assert run("你好。", "今天天气不错。") == [{"type": "text", "text": "你好。今天天气不错。", "spoken": True}]


def test_emotion_and_action() -> None:
    assert run("[happy]你好。[nod]请多指教。") == [
        {"type": "emotion", "name": "happy"},
        {"type": "text", "text": "你好。", "spoken": True},
        {"type": "action", "name": "nod"},
        {"type": "text", "text": "请多指教。", "spoken": True},
    ]


def test_move_tags() -> None:
    # 走到屏幕上的某个位置：最长的 [go-bottomright] 拆成几块也认；不认识的位置原样留着
    assert run("[go-bottom", "right]好，我回去了。") == [
        {"type": "move", "spot": "bottomright"},
        {"type": "text", "text": "好，我回去了。", "spoken": True},
    ]
    assert run("[go-topleft]嗯。")[0] == {"type": "move", "spot": "topleft"}
    assert run("[go-moon]去不了。") == [{"type": "text", "text": "[go-moon]去不了。", "spoken": True}]


def test_tag_split_across_chunks() -> None:
    assert run("好的。[ha", "pp", "y]谢谢。") == [
        {"type": "text", "text": "好的。", "spoken": True},
        {"type": "emotion", "name": "happy"},
        {"type": "text", "text": "谢谢。", "spoken": True},
    ]


def test_split_tag_waits_instead_of_leaking() -> None:
    parser = TagParser()
    assert parser.feed("嗯[ti") == [{"type": "text", "text": "嗯", "spoken": True}]
    assert parser.feed("lt]") == [{"type": "action", "name": "tilt"}]


def test_code_brackets_are_kept() -> None:
    code = "for i in range(n): a[i] = b[i - 1] + c[0]"
    assert run(code) == [{"type": "text", "text": code, "spoken": True}]


def test_unknown_tag_is_kept_as_text() -> None:
    assert run("[dance]转一圈") == [{"type": "text", "text": "[dance]转一圈", "spoken": True}]


def test_alias_and_case() -> None:
    assert run("[Smile]嗯。") == [{"type": "emotion", "name": "happy"}, {"type": "text", "text": "嗯。", "spoken": True}]


def test_space_after_tag_is_removed() -> None:
    assert run("[nod] ", " 好的") == [{"type": "action", "name": "nod"}, {"type": "text", "text": "好的", "spoken": True}]


def test_unclosed_bracket_is_flushed_at_end() -> None:
    assert run("看这里 [") == [{"type": "text", "text": "看这里 [", "spoken": True}]


def test_long_bracket_is_released_without_waiting() -> None:
    parser = TagParser()
    events = parser.feed("[" + "x" * 20)
    assert events == [{"type": "text", "text": "[" + "x" * 20, "spoken": True}]


def test_nested_bracket_before_tag() -> None:
    assert run("[[happy]") == [{"type": "text", "text": "[", "spoken": True}, {"type": "emotion", "name": "happy"}]


def test_spoken_tags_survive_every_split_and_preserve_emotions() -> None:
    text = "[happy][say]查到了。[nod]细节在下面。[/say][relaxed]\n  1. 链接"
    expected = [
        {"type": "emotion", "name": "happy"},
        {"type": "text", "text": "查到了。", "spoken": True},
        {"type": "action", "name": "nod"},
        {"type": "text", "text": "细节在下面。", "spoken": True},
        {"type": "emotion", "name": "relaxed"},
        {"type": "text", "text": "\n  1. 链接", "spoken": False},
    ]
    for i in range(len(text) + 1):
        assert run(text[:i], text[i:]) == expected
    assert run(*text) == expected


@pytest.mark.parametrize(("summary", "detail"), [
    ("第一句。第二句！\n", "第三句。"),
    ("第一行\n第二行\n", "第三行"),
    ("甲" * 58 + "，A1！", "乙"),
    ("查到了。\n  ", "- 一项"),
    ("查到了。\n", "* 一项"),
    ("查到了。\n", "1. 一项"),
    ("建议写法：\n", "```python\na[i] = 1\n```"),
    ("链接是 ", "https://example.com"),
    ("链接是 ", "http://example.com"),
    ("链接是 ", "www.example.com"),
    ("İ 的链接是 ", "HTTPS://example.com"),
    ("", "https://example.com"),
])
def test_fallback_boundaries_survive_every_split(summary: str, detail: str) -> None:
    text = summary + detail
    expected: list[Event] = [
        *([{"type": "text", "text": summary, "spoken": True}] if summary else []),
        {"type": "text", "text": detail, "spoken": False},
        {"type": "text", "text": "", "spoken": True, "hint": "more"},
    ]
    for i in range(len(text) + 1):
        assert run(text[:i], text[i:]) == expected
    assert run(*text) == expected


def test_short_answer_and_unclosed_tag_stay_intact() -> None:
    for text in ("简单的一句。", "看这里 [say", "a[i]", "写了 60 个字。\n", "[dance]转一圈", "**加粗**"):
        assert run(*text) == [{"type": "text", "text": text, "spoken": True}]
    assert run("[say]一句简答。[/say]") == [{"type": "text", "text": "一句简答。", "spoken": True}]
    assert run("[say]开头[/say][say]后面[/say]") == [
        {"type": "text", "text": "开头", "spoken": True},
        {"type": "text", "text": "后面", "spoken": False},
    ]
