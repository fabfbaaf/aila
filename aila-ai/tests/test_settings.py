"""设置接口：读的时候不泄露密钥、写回 .env 并当场生效、参数检查；以及只接受本机请求"""

import os
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import deps, envfile
from app.llm import MockModel, OpenAICompatibleModel
from app.main import app

EXAMPLE = """# 大模型
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
# 留空时用假模型
LLM_API_KEY=

SEARCH_PROVIDER=bocha
"""


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """设置写到临时的 .env；测完把进程环境变量、配置和缓存都恢复"""
    path = tmp_path / ".env"
    path.write_text(EXAMPLE, encoding="utf-8")
    monkeypatch.setattr(deps, "ENV_FILE", path)
    monkeypatch.setattr(deps, "get_voice", lambda: None)  # 别在测试里真的去加载语音模型
    monkeypatch.setattr(deps, "get_ears", lambda: None)
    saved_env, saved_settings = dict(os.environ), deps.settings
    for key in ("LLM_API_KEY", "SEARCH_API_KEY"):
        os.environ.pop(key, None)
    monkeypatch.setenv("AILA_DATA_DIR", str(tmp_path / "data"))  # 别在 aila-ai/data 里建资料库
    deps.settings = replace(deps.settings, llm_api_key="", search_api_key="", data_dir=tmp_path / "data")
    for cached in (deps.get_model, deps.get_registry, deps.get_knowledge):
        cached.cache_clear()
    yield path
    os.environ.clear()
    os.environ.update(saved_env)
    deps.settings = saved_settings
    for cached in (deps.get_model, deps.get_registry, deps.get_knowledge):
        cached.cache_clear()


def test_api_key_is_write_only(env: Path) -> None:
    client = TestClient(app)
    res = client.put("/settings", json={"llmApiKey": "sk-test-123", "llmModel": "deepseek-reasoner"})
    assert res.status_code == 200
    assert "model" in res.json()["reloaded"]

    body = client.get("/settings").text
    assert "sk-test-123" not in body  # 读的时候只告诉设没设
    assert client.get("/settings").json()["llm"] == {"baseUrl": "https://api.deepseek.com", "model": "deepseek-reasoner", "apiKeySet": True}

    text = env.read_text(encoding="utf-8")
    assert "LLM_API_KEY=sk-test-123" in text
    assert "# 留空时用假模型" in text  # 注释原样保留

    model = deps.get_model()
    assert isinstance(model, OpenAICompatibleModel) and model.name == "deepseek-reasoner"  # 当场生效


def test_clearing_the_key_goes_back_to_the_mock_model(env: Path) -> None:
    client = TestClient(app)
    client.put("/settings", json={"llmApiKey": "sk-test-123"})
    client.put("/settings", json={"llmApiKey": ""})
    assert isinstance(deps.get_model(), MockModel)
    assert client.post("/settings/test").json() == {"ok": False, "message": "还没有填大模型密钥，现在用的是内置的假模型"}


def test_search_provider_and_voice_settings(env: Path) -> None:
    client = TestClient(app)
    res = client.put("/settings", json={"searchProvider": "none", "ttsSpeed": 1.2, "endSilence": 0.6, "asrLanguage": "auto"})
    assert set(res.json()["reloaded"]) == {"search", "tts", "ears"}
    voice = res.json()["settings"]["voice"]
    assert (voice["ttsSpeed"], voice["endSilence"], voice["asrLanguage"]) == (1.2, 0.6, "auto")
    assert "web_search" not in deps.get_registry().toolbox().names  # 关掉搜索后只剩读网页


@pytest.mark.parametrize(
    "body",
    [
        {"llmBaseUrl": "ftp://example.com"},
        {"llmApiKey": "sk with space"},
        {"ttsSpeed": 3},
        {"wakeThreshold": 0},
        {"searchProvider": "google"},
        {"asrLanguage": "fr"},
    ],
)
def test_validation(env: Path, body: dict[str, object]) -> None:
    assert TestClient(app).put("/settings", json=body).status_code == 422
    assert env.read_text(encoding="utf-8") == EXAMPLE  # 不合法的一个字都不写


def test_engine_must_be_downloaded(env: Path, tmp_path: Path) -> None:
    deps.settings = replace(deps.settings, models_dir=tmp_path / "no-models")
    res = TestClient(app).put("/settings", json={"ttsEngine": "kokoro"})
    assert res.status_code == 422 and "还没下载" in res.json()["detail"]


def test_envfile_update(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    (tmp_path / ".env.example").write_text(EXAMPLE, encoding="utf-8")
    envfile.update(path, {"LLM_MODEL": "qwen-plus", "AILA_TTS_SPEED": "1.1", "NOTE": "有 空格 # 和井号"})
    text = path.read_text(encoding="utf-8")
    assert "LLM_MODEL=qwen-plus" in text and "LLM_MODEL=deepseek-chat" not in text
    assert text.endswith('AILA_TTS_SPEED=1.1\nNOTE="有 空格 # 和井号"\n')

    from dotenv import dotenv_values

    assert dotenv_values(path)["NOTE"] == "有 空格 # 和井号"  # 能原样读回来


@pytest.mark.parametrize("origin", ["https://evil.example", "http://localhost.evil.example", "http://192.168.1.5:5180"])
def test_other_websites_cannot_call_the_service(origin: str) -> None:
    client = TestClient(app)
    assert client.get("/health", headers={"origin": origin}).status_code == 403
    assert client.put("/settings", json={"llmModel": "x"}, headers={"origin": origin}).status_code == 403


@pytest.mark.parametrize("origin", [None, "http://localhost:5173", "http://127.0.0.1:5180", "file://"])
def test_local_callers_are_fine(origin: str | None) -> None:
    headers = {"origin": origin} if origin else {}
    assert TestClient(app).get("/settings", headers=headers).status_code == 200
