from app.tags import TagParser


def run(*chunks: str) -> list[dict[str, str]]:
    """把几块文本依次喂进去，相邻的文字事件合并，方便断言"""
    parser = TagParser()
    events = [e for c in chunks for e in parser.feed(c)] + parser.flush()
    merged: list[dict[str, str]] = []
    for e in events:
        if e["type"] == "text" and merged and merged[-1]["type"] == "text":
            merged[-1] = {"type": "text", "text": merged[-1]["text"] + e["text"]}
        else:
            merged.append(e)
    return merged


def test_plain_text() -> None:
    assert run("你好。", "今天天气不错。") == [{"type": "text", "text": "你好。今天天气不错。"}]


def test_emotion_and_action() -> None:
    assert run("[happy]你好。[nod]请多指教。") == [
        {"type": "emotion", "name": "happy"},
        {"type": "text", "text": "你好。"},
        {"type": "action", "name": "nod"},
        {"type": "text", "text": "请多指教。"},
    ]


def test_tag_split_across_chunks() -> None:
    assert run("好的。[ha", "pp", "y]谢谢。") == [
        {"type": "text", "text": "好的。"},
        {"type": "emotion", "name": "happy"},
        {"type": "text", "text": "谢谢。"},
    ]


def test_split_tag_waits_instead_of_leaking() -> None:
    parser = TagParser()
    assert parser.feed("嗯[ti") == [{"type": "text", "text": "嗯"}]
    assert parser.feed("lt]") == [{"type": "action", "name": "tilt"}]


def test_code_brackets_are_kept() -> None:
    code = "for i in range(n): a[i] = b[i - 1] + c[0]"
    assert run(code) == [{"type": "text", "text": code}]


def test_unknown_tag_is_kept_as_text() -> None:
    assert run("[dance]转一圈") == [{"type": "text", "text": "[dance]转一圈"}]


def test_alias_and_case() -> None:
    assert run("[Smile]嗯。") == [{"type": "emotion", "name": "happy"}, {"type": "text", "text": "嗯。"}]


def test_space_after_tag_is_removed() -> None:
    assert run("[nod] ", " 好的") == [{"type": "action", "name": "nod"}, {"type": "text", "text": "好的"}]


def test_unclosed_bracket_is_flushed_at_end() -> None:
    assert run("看这里 [") == [{"type": "text", "text": "看这里 ["}]


def test_long_bracket_is_released_without_waiting() -> None:
    parser = TagParser()
    events = parser.feed("[" + "x" * 20)
    assert events == [{"type": "text", "text": "[" + "x" * 20}]


def test_nested_bracket_before_tag() -> None:
    assert run("[[happy]") == [{"type": "text", "text": "["}, {"type": "emotion", "name": "happy"}]
