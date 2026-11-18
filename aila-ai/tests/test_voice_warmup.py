"""语音模型用到时再加载：启动不加载；/voice/warmup 在后台加载要的那个，不重复加载"""

import threading
import time

import pytest
from fastapi.testclient import TestClient

from app import deps
from app.main import app
from app.routes import voice as voice_routes


@pytest.fixture
def fresh_status(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(deps.status, "tts", "idle")
    monkeypatch.setitem(deps.status, "ears", "idle")
    yield deps.status


def test_warmup_loads_only_what_is_asked_once(fresh_status, monkeypatch: pytest.MonkeyPatch) -> None:
    loads: list[str] = []
    gate = threading.Event()

    def fake_voice():
        loads.append("tts")
        gate.wait(2)
        deps.status["tts"] = "ready"

    monkeypatch.setattr(voice_routes, "get_voice", fake_voice)
    monkeypatch.setattr(voice_routes, "get_ears", lambda: loads.append("ears"))
    client = TestClient(app)  # 不进启动流程：那里会真的加载向量模型
    first = client.post("/voice/warmup", json={"tts": True}).json()
    assert first == {"tts": "loading", "ears": "idle"}  # 马上返回，不等加载完
    assert client.post("/voice/warmup", json={"tts": True}).json()["tts"] == "loading"  # 正在加载的不重复
    gate.set()
    for _ in range(50):
        if deps.status["tts"] == "ready":
            break
        time.sleep(0.02)
    assert client.post("/voice/warmup", json={"tts": True}).json()["tts"] == "ready"
    assert loads == ["tts"]  # 只加载了一次，也没碰「听」


def test_service_start_does_not_load_voice(fresh_status, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import main

    called: list[str] = []
    monkeypatch.setattr(deps, "_voice", lambda: called.append("tts"))
    monkeypatch.setattr(deps, "_ears", lambda: called.append("ears"))
    monkeypatch.setattr(main, "get_embedder", lambda: None)  # 向量模型照常预加载，这里换成空的，免得真去加载
    monkeypatch.setattr(deps, "start_mcp", lambda *a, **k: None)
    with TestClient(app):
        pass
    assert called == [] and deps.status["tts"] == "idle"
