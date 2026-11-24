"""界面语言：英文时她用英文回答（对话的系统提示词、编程会话的开头），中文时提示词和以前一字不差"""

from pathlib import Path

from app.persona import ENGLISH, SYSTEM_PROMPT, persona_addendum, system_prompt
from app.runtime.runner import ENGLISH_NOTE, Runtime

from .test_runtime import SHELL, Scripted


def test_chat_prompt_follows_language() -> None:
    assert system_prompt() == SYSTEM_PROMPT  # 默认中文：前缀缓存不断
    assert system_prompt(language="en") == SYSTEM_PROMPT + ENGLISH
    assert "English" in persona_addendum(language="en")
    both = persona_addendum("calm", "", "", "en")
    assert both.index("说话风格") < both.index("English")  # 性格在前，语言在最后


def test_coding_session_context_follows_language(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    lang = {"v": "zh"}
    rt = Runtime(tmp_path / "data", lambda: Scripted([["好"]]), shell=SHELL, language=lambda: lang["v"])
    assert ENGLISH_NOTE not in rt.create(str(root)).events[0]["context"]
    lang["v"] = "en"
    assert ENGLISH_NOTE in rt.create(str(root)).events[0]["context"]


def test_settings_roundtrip(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    from fastapi.testclient import TestClient

    from app import deps
    from app.main import app

    monkeypatch.setattr(deps, "ENV_FILE", tmp_path / ".env")
    client = TestClient(app)
    assert client.get("/settings").json()["language"] == "zh"
    res = client.put("/settings", json={"language": "en"})
    assert res.status_code == 200 and res.json()["settings"]["language"] == "en"
    assert client.put("/settings", json={"language": "fr"}).status_code == 422
    client.put("/settings", json={"language": "zh"})
