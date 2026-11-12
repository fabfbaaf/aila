"""「耳朵」的状态机：唤醒、断句、识别结果处理。模型换成假的，按剧本出结果。"""

import numpy as np

from app.voice.audio import SAMPLE_RATE, float_to_wav, pcm16_to_float
from app.voice.listener import PREROLL_SECONDS, Listener, Transcript

CHUNK = SAMPLE_RATE // 10  # 100ms


def chunk(value: float = 0.0, n: int = CHUNK) -> np.ndarray:
    return np.full(n, value, dtype=np.float32)


class FakeWake:
    """收到值接近 1.0 的音频块时「听到唤醒词」（经过 16 位 PCM 后不是正好 1.0）"""

    def __init__(self) -> None:
        self.resets = 0

    def accept(self, samples: np.ndarray) -> str | None:
        return "你好艾拉" if samples.size and samples[-1] > 0.9 else None

    def reset(self) -> None:
        self.resets += 1


class FakeSegmenter:
    """值大于 0.25 的块算说话；说话后遇到静音就把整段交出来"""

    def __init__(self) -> None:
        self.buf: list[np.ndarray] = []
        self.fed = 0

    @property
    def speaking(self) -> bool:
        return bool(self.buf)

    def accept(self, samples: np.ndarray) -> tuple[bool, list[np.ndarray]]:
        self.fed += len(samples)
        voiced = samples[samples > 0.25]
        started = voiced.size > 0 and not self.buf
        if voiced.size:
            self.buf.append(voiced)
            return started, []
        if self.buf:
            seg = np.concatenate(self.buf)
            self.buf = []
            return False, [seg]
        return False, []

    def reset(self) -> None:
        self.buf = []


class FakeAsr:
    def __init__(self, *texts: str) -> None:
        self.texts = list(texts)
        self.calls = 0

    def transcribe(self, samples: np.ndarray) -> Transcript:
        self.calls += 1
        return Transcript(text=self.texts.pop(0), emotion="neutral")


def make(*texts: str) -> tuple[Listener, FakeSegmenter, FakeAsr]:
    seg, asr = FakeSegmenter(), FakeAsr(*texts)
    return Listener(FakeWake(), seg, asr), seg, asr


def run(listener: Listener, *chunks: np.ndarray) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for c in chunks:
        events += listener.feed(c)
    return [{k: v for k, v in e.items() if k != "asr_ms"} for e in events]


def test_wake_then_command_in_one_breath() -> None:
    listener, _, _ = make("你好艾拉，明天天气怎么样？")
    events = run(listener, chunk(0), chunk(0.5), chunk(1.0), chunk(0.5), chunk(0))

    assert events == [
        {"type": "wake", "keyword": "你好艾拉"},
        {"type": "speech_start"},
        {"type": "speech_end", "seconds": 0.3},  # 唤醒词前后的音频都在这一段里
        {"type": "transcript", "text": "明天天气怎么样？", "emotion": "neutral"},
    ]
    assert listener.mode == "listen"


def test_wake_word_alone_is_empty_then_next_sentence_is_kept_whole() -> None:
    listener, _, _ = make("你好，爱拉。", "你好，今天几号")
    events = run(listener, chunk(1.0), chunk(0), chunk(0.5), chunk(0))

    assert [e["type"] for e in events] == ["wake", "speech_start", "speech_end", "empty", "speech_start", "speech_end", "transcript"]
    # 唤醒后的第二句话里的「你好」不能被当成唤醒词去掉
    assert events[-1]["text"] == "你好，今天几号"


def test_no_wake_word_means_no_listening() -> None:
    listener, seg, asr = make()
    assert run(listener, chunk(0.5), chunk(0.5), chunk(0)) == []
    assert seg.fed == 0 and asr.calls == 0  # 没唤醒时音频不进断句和识别


def test_listen_mode_without_wake_word() -> None:
    listener, _, _ = make("嗯……", "等一下")
    listener.set_mode("listen")
    events = run(listener, chunk(0.5), chunk(0), chunk(0.5), chunk(0))

    assert [e["type"] for e in events] == ["speech_start", "speech_end", "empty", "speech_start", "speech_end", "transcript"]
    assert events[-1]["text"] == "等一下"


def test_off_mode_ignores_everything() -> None:
    listener, seg, _ = make()
    listener.set_mode("off")
    assert run(listener, chunk(1.0), chunk(0.5)) == []
    assert seg.fed == 0


def test_preroll_is_bounded() -> None:
    listener, seg, _ = make("你好艾拉")
    silence = [chunk(0) for _ in range(int(PREROLL_SECONDS * 10) * 3)]
    run(listener, *silence, chunk(1.0))
    # 交给断句的只有最近 PREROLL_SECONDS 秒左右
    assert seg.fed <= (PREROLL_SECONDS + 0.1) * SAMPLE_RATE + CHUNK


def test_switching_mode_resets_state() -> None:
    listener, seg, _ = make()
    listener.set_mode("listen")
    run(listener, chunk(0.5))
    assert seg.speaking
    listener.set_mode("wake")
    assert not seg.speaking


def test_pcm_round_trip() -> None:
    samples = np.array([0.0, 0.5, -0.5, 0.999], dtype=np.float32)
    pcm = (samples * 32767).astype("<i2").tobytes()
    assert np.allclose(pcm16_to_float(pcm), samples, atol=1e-4)
    assert pcm16_to_float(pcm + b"\x01").shape == (4,)  # 半个采样点丢掉

    wav = float_to_wav(samples, 24000)
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
    assert int.from_bytes(wav[24:28], "little") == 24000
