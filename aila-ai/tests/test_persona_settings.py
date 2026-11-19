"""性格设置：说话风格、怎么称呼你、口头禅接在系统提示词最后；默认时系统提示词一字不差（缓存照样命中）"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import deps
from app.main import app
from app.persona import SYSTEM_PROMPT, persona_addendum, system_prompt
from app.routes.chat import ChatMessage, build_messages

from .test_settings import env  # noqa: F401  （env 是 fixture：临时的 .env）


def test_default_prompt_is_unchanged() -> None:
    assert persona_addendum() == "" and system_prompt() == SYSTEM_PROMPT
    assert build_messages([ChatMessage(role="user", content="你好")], 20)[0]["content"] == SYSTEM_PROMPT


def test_addendum_lines() -> None:
    text = persona_addendum("teasing", "小林", "喵")
    assert "爱吐槽" in text and "叫他「小林」" in text and "「喵」" in text
    assert persona_addendum("unknown-style") == ""
    assert "\n" not in persona_addendum("gentle", "a\nb").split("叫他")[1].split("」")[0]  # 换行这类字符去掉
    msgs = build_messages([ChatMessage(role="user", content="你好")], 20, system=system_prompt("calm"))
    assert msgs[0]["content"].startswith(SYSTEM_PROMPT) and "冷静一点" in msgs[0]["content"]


def test_settings_route(env: Path) -> None:  # noqa: F811
    client = TestClient(app)
    assert client.get("/settings").json()["persona"] == {"style": "gentle", "call": "", "catchphrase": ""}
    r = client.put("/settings", json={"personaStyle": "lively", "personaCall": "林同学", "personaCatchphrase": "冲"})
    assert r.status_code == 200 and r.json()["settings"]["persona"] == {"style": "lively", "call": "林同学", "catchphrase": "冲"}
    assert deps.settings.persona_style == "lively" and "AILA_PERSONA_CALL=林同学" in env.read_text(encoding="utf-8")
    assert client.put("/settings", json={"personaCall": "a\nb"}).status_code == 422
    assert client.put("/settings", json={"personaStyle": "evil"}).status_code == 422
    assert client.put("/settings", json={"personaCatchphrase": "x" * 13}).status_code == 422
