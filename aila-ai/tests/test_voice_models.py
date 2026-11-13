"""用真实模型走一遍：合成「你好艾拉，……」，再让耳朵去听。没下载模型时跳过。"""

import re
from pathlib import Path

import numpy as np
import pytest

from app.voice.audio import SAMPLE_RATE

MODELS = Path(__file__).resolve().parent.parent / "models"

pytest.importorskip("sherpa_onnx")
from app.voice.engines import EarConfig, Ears, missing_files  # noqa: E402
from app.voice.tts import TtsConfig, Voice  # noqa: E402

pytestmark = pytest.mark.skipif(
    bool(missing_files(MODELS)) or not (MODELS / "vits-melo-tts-zh_en").exists(),
    reason="没有下载语音模型（scripts/download_models.py）",
)


@pytest.fixture(scope="module")
def voice() -> Voice:
    return Voice(TtsConfig(MODELS, "melo", deterministic=True))  # 合成结果固定，测试不会时好时坏


@pytest.fixture(scope="module")
def ears() -> Ears:
    return Ears(EarConfig(MODELS))


def speak(voice: Voice, text: str) -> np.ndarray:
    """合成后降到 16kHz。先在频域去掉 8kHz 以上的成分，否则直接插值会混叠，识别率明显下降"""
    samples = voice.synthesize(text)
    assert samples is not None
    spec = np.fft.rfft(samples)
    spec[np.fft.rfftfreq(len(samples), 1 / voice.sample_rate) > 7600] = 0
    filtered = np.fft.irfft(spec, len(samples))
    n = int(len(filtered) * SAMPLE_RATE / voice.sample_rate)
    return np.interp(np.linspace(0, len(filtered) - 1, n), np.arange(len(filtered)), filtered).astype(np.float32)


def silence(seconds: float) -> np.ndarray:
    return (np.random.default_rng(0).standard_normal(int(SAMPLE_RATE * seconds)) * 0.002).astype(np.float32)


def listen(ears: Ears, audio: np.ndarray) -> list[dict[str, object]]:
    listener = ears.new_listener()
    events: list[dict[str, object]] = []
    for i in range(0, len(audio), SAMPLE_RATE // 10):
        events += listener.feed(audio[i : i + SAMPLE_RATE // 10])
    return events


def test_wake_word_and_command(voice: Voice, ears: Ears) -> None:
    audio = np.concatenate([silence(1), speak(voice, "你好艾拉，明天天气怎么样？"), silence(1.5)])
    events = listen(ears, audio)
    types = [e["type"] for e in events]

    assert types[0] == "wake"
    transcripts = [e for e in events if e["type"] == "transcript"]
    assert len(transcripts) == 1
    assert "天气" in str(transcripts[0]["text"])
    assert "艾拉" not in str(transcripts[0]["text"])


def test_mixed_chinese_english_command(voice: Voice, ears: Ears) -> None:
    listener = ears.new_listener()
    listener.set_mode("listen")
    audio = np.concatenate([silence(0.5), speak(voice, "帮我搜一下 Python 3.13 新特性"), silence(1.5)])
    events = []
    for i in range(0, len(audio), SAMPLE_RATE // 10):
        events += listener.feed(audio[i : i + SAMPLE_RATE // 10])
    text = next(str(e["text"]) for e in events if e["type"] == "transcript").lower()
    # 英文词要被识别成字母（而不是「拍站」这种谐音），版本号和中文部分都对
    assert re.search(r"py\w*on", text), text
    assert "3.13" in text and "新特性" in text, text


def test_no_wake_word_no_reaction(voice: Voice, ears: Ears) -> None:
    audio = np.concatenate([silence(1), speak(voice, "今天的会议改到下午三点。"), silence(1.5)])
    assert listen(ears, audio) == []


def test_synthesis_handles_mixed_language(voice: Voice) -> None:
    samples = voice.synthesize("Vue 3.5 发布了。")
    assert samples is not None
    assert 0.8 < len(samples) / voice.sample_rate < 5
    assert voice.synthesize("……") is None
