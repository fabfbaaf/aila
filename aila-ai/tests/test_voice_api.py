"""语音接口：/tts 返回 WAV，/voice 收 PCM 推事件、切换模式、拒绝外部网页"""

from collections.abc import Iterator

import numpy as np
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app, get_ears, get_voice
from app.voice.listener import Listener
from app.voice.tts import clean_for_speech

from .test_listener import FakeAsr, FakeSegmenter, FakeWake


class FakeVoice:
    name = "fake"
    sample_rate = 16000

    def __init__(self) -> None:
        self.texts: list[str] = []

    def synthesize(self, text: str, speed: float | None = None) -> np.ndarray | None:
        self.texts.append(text)
        return None if not clean_for_speech(text) else np.zeros(1600, dtype=np.float32)


class FakeEars:
    def __init__(self, *texts: str) -> None:
        self.texts = texts

    def new_listener(self) -> Listener:
        return Listener(FakeWake(), FakeSegmenter(), FakeAsr(*self.texts))


@pytest.fixture
def client() -> Iterator[TestClient]:
    yield TestClient(app)
    app.dependency_overrides.clear()


def pcm(value: float, n: int = 1600) -> bytes:
    return (np.full(n, value, dtype=np.float32) * 32767).astype("<i2").tobytes()


def test_tts_returns_wav(client: TestClient) -> None:
    voice = FakeVoice()
    app.dependency_overrides[get_voice] = lambda: voice
    res = client.post("/tts", json={"text": "我查一下。"})

    assert res.status_code == 200
    assert res.headers["content-type"] == "audio/wav"
    assert res.content[:4] == b"RIFF"
    assert int(res.headers["x-synth-ms"]) >= 0


def test_tts_nothing_to_say(client: TestClient) -> None:
    app.dependency_overrides[get_voice] = FakeVoice
    assert client.post("/tts", json={"text": "……"}).status_code == 204


def test_tts_unavailable(client: TestClient) -> None:
    app.dependency_overrides[get_voice] = lambda: None
    res = client.post("/tts", json={"text": "你好"})
    assert res.status_code == 503


def test_voice_wake_and_transcript(client: TestClient) -> None:
    app.dependency_overrides[get_ears] = lambda: FakeEars("你好艾拉，几点了")
    with client.websocket_connect("/voice") as ws:
        assert ws.receive_json() == {"type": "ready", "mode": "wake"}
        ws.send_bytes(pcm(0.0))
        ws.send_bytes(pcm(1.0))  # 假唤醒器：接近 1.0 的块就是唤醒词
        assert ws.receive_json() == {"type": "wake", "keyword": "你好艾拉"}
        assert ws.receive_json() == {"type": "speech_start"}
        ws.send_bytes(pcm(0.0))
        assert ws.receive_json()["type"] == "speech_end"
        event = ws.receive_json()
        assert event["type"] == "transcript" and event["text"] == "几点了"
        # 桌面端说「这次对话结束了」，回到等唤醒词
        ws.send_text('{"type": "mode", "mode": "wake"}')
        assert ws.receive_json() == {"type": "mode", "mode": "wake"}


def test_voice_listen_mode_events(client: TestClient) -> None:
    app.dependency_overrides[get_ears] = lambda: FakeEars("帮我查一下天气")
    with client.websocket_connect("/voice") as ws:
        ws.receive_json()
        ws.send_text('{"type": "mode", "mode": "listen"}')
        assert ws.receive_json()["mode"] == "listen"
        ws.send_bytes(pcm(0.5))
        assert ws.receive_json() == {"type": "speech_start"}
        ws.send_bytes(pcm(0.0))
        assert ws.receive_json()["type"] == "speech_end"
        event = ws.receive_json()
        assert event["type"] == "transcript" and event["text"] == "帮我查一下天气"


def test_voice_ignores_bad_commands(client: TestClient) -> None:
    app.dependency_overrides[get_ears] = lambda: FakeEars()
    with client.websocket_connect("/voice") as ws:
        ws.receive_json()
        ws.send_text("not json")
        ws.send_text('{"type": "mode", "mode": "shout"}')
        ws.send_text('{"type": "mode", "mode": "off"}')
        assert ws.receive_json() == {"type": "mode", "mode": "off"}  # 前两条没有回应


def test_voice_unavailable(client: TestClient) -> None:
    app.dependency_overrides[get_ears] = lambda: None
    with client.websocket_connect("/voice") as ws:
        assert ws.receive_json()["type"] == "error"


@pytest.mark.parametrize("origin", ["https://evil.example", "http://localhost.evil.example", "http://192.168.1.5:5173"])
def test_voice_rejects_other_websites(client: TestClient, origin: str) -> None:
    app.dependency_overrides[get_ears] = lambda: FakeEars()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/voice", headers={"origin": origin}) as ws:
            ws.receive_json()


@pytest.mark.parametrize("origin", ["http://localhost:5173", "http://127.0.0.1:5180", "file://"])
def test_voice_accepts_local_pages(client: TestClient, origin: str) -> None:
    app.dependency_overrides[get_ears] = lambda: FakeEars()
    with client.websocket_connect("/voice", headers={"origin": origin}) as ws:
        assert ws.receive_json()["type"] == "ready"


@pytest.mark.parametrize(
    ("text", "spoken"),
    [
        ("……我在。", "我在。"),
        ("我看了 https://docs.python.org/3/ 这个页面。", "我看了 这个页面。"),
        ("**重点**是「三点」。", "重点是三点。"),
        ("好的😊", "好的"),
        ("……", ""),
        ("https://a.example", ""),
    ],
)
def test_clean_for_speech(text: str, spoken: str) -> None:
    assert clean_for_speech(text) == spoken
