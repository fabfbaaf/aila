"""sherpa-onnx 模型：唤醒词、断句、识别。全部在本机 CPU 上跑。"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import sherpa_onnx as so

from .audio import SAMPLE_RATE
from .listener import Listener, Transcript

# 文件名和 scripts/download_models.py 里的一致
KWS_DIR = "sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20"
KWS_SUFFIX = "epoch-13-avg-2-chunk-16-left-64.int8.onnx"
VAD_FILE = "silero_vad.onnx"
ASR_DIR = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"

# 唤醒词：拼音按声母、韵母拆开，带声调。@ 后面是识别到时返回的名字
WAKE_KEYWORDS = "n ǐ h ǎo ài l ā @你好艾拉\n"


@dataclass(frozen=True)
class EarConfig:
    models_dir: Path
    # 唤醒灵敏度：越低越容易唤醒，也越容易误唤醒
    wake_threshold: float = 0.25
    # 停顿多久算说完。太短会把一句话切断，太长她反应慢
    end_silence: float = 0.4
    max_speech_seconds: float = 20.0
    threads: int = 2
    # 识别语种：zh 对短句（「等一下」）更准，中英混说照样能认；auto 适合多语种用户
    language: str = "zh"


def missing_files(models_dir: Path) -> list[str]:
    needed = [
        f"{KWS_DIR}/encoder-{KWS_SUFFIX}",
        VAD_FILE,
        f"{ASR_DIR}/model.int8.onnx",
    ]
    return [n for n in needed if not (models_dir / n).exists()]


class SherpaWake:
    def __init__(self, spotter: so.KeywordSpotter) -> None:
        self._kws = spotter
        self._stream = spotter.create_stream()

    def accept(self, samples: np.ndarray) -> str | None:
        self._stream.accept_waveform(SAMPLE_RATE, samples)
        while self._kws.is_ready(self._stream):
            self._kws.decode_stream(self._stream)
            keyword = self._kws.get_result(self._stream)
            if keyword:
                self._kws.reset_stream(self._stream)
                return keyword
        return None

    def reset(self) -> None:
        self._stream = self._kws.create_stream()


class SherpaSegmenter:
    """Silero VAD。它要求每次喂固定长度的窗口，零头先存着"""

    def __init__(self, config: so.VadModelConfig) -> None:
        self._config = config
        self._window = config.silero_vad.window_size
        self._vad = so.VoiceActivityDetector(config, buffer_size_in_seconds=60)
        self._pending = np.zeros(0, dtype=np.float32)
        self._was_speaking = False

    @property
    def speaking(self) -> bool:
        return self._vad.is_speech_detected()

    def accept(self, samples: np.ndarray) -> tuple[bool, list[np.ndarray]]:
        buf = np.concatenate([self._pending, samples])
        started = False
        segments: list[np.ndarray] = []
        i = 0
        while i + self._window <= len(buf):
            self._vad.accept_waveform(buf[i : i + self._window])
            i += self._window
            now = self._vad.is_speech_detected()
            started |= now and not self._was_speaking
            self._was_speaking = now
            while not self._vad.empty():
                segments.append(np.array(self._vad.front.samples, dtype=np.float32))
                self._vad.pop()
        self._pending = buf[i:]
        return started, segments

    def reset(self) -> None:
        self._vad.reset()
        self._pending = np.zeros(0, dtype=np.float32)
        self._was_speaking = False


class SenseVoice:
    def __init__(self, recognizer: so.OfflineRecognizer) -> None:
        self._rec = recognizer

    def transcribe(self, samples: np.ndarray) -> Transcript:
        stream = self._rec.create_stream()
        stream.accept_waveform(SAMPLE_RATE, samples)
        self._rec.decode_stream(stream)
        r = stream.result
        # SenseVoice 还会给出语种、情绪（<|HAPPY|>）和声音事件（<|Speech|>、<|BGM|>）
        return Transcript(text=r.text, lang=_tag(r.lang), emotion=_tag(r.emotion), event=_tag(r.event))


def _tag(value: str) -> str:
    return value.strip("<|>").lower()


class Ears:
    """加载一次，多条连接共用；每条连接用 new_listener() 拿自己的状态"""

    def __init__(self, cfg: EarConfig) -> None:
        missing = missing_files(cfg.models_dir)
        if missing:
            raise FileNotFoundError("缺少语音模型：" + "、".join(missing) + "（运行 scripts/download_models.py）")
        self._cfg = cfg
        kws = cfg.models_dir / KWS_DIR
        keywords_file = kws / "aila-keywords.txt"
        keywords_file.write_text(WAKE_KEYWORDS, encoding="utf-8")
        self._spotter = so.KeywordSpotter(
            tokens=str(kws / "tokens.txt"),
            encoder=str(kws / f"encoder-{KWS_SUFFIX}"),
            decoder=str(kws / f"decoder-{KWS_SUFFIX.replace('.int8', '')}"),
            joiner=str(kws / f"joiner-{KWS_SUFFIX}"),
            keywords_file=str(keywords_file),
            keywords_threshold=cfg.wake_threshold,
            num_threads=1,
        )
        self._vad_config = so.VadModelConfig(
            silero_vad=so.SileroVadModelConfig(
                model=str(cfg.models_dir / VAD_FILE),
                threshold=0.5,
                min_silence_duration=cfg.end_silence,
                min_speech_duration=0.25,
                max_speech_duration=cfg.max_speech_seconds,
            ),
            sample_rate=SAMPLE_RATE,
            num_threads=1,
        )
        asr = cfg.models_dir / ASR_DIR
        self._recognizer = so.OfflineRecognizer.from_sense_voice(
            model=str(asr / "model.int8.onnx"),
            tokens=str(asr / "tokens.txt"),
            num_threads=cfg.threads,
            language=cfg.language,
            use_itn=True,  # 加标点，数字写成阿拉伯数字
        )

    def new_listener(self) -> Listener:
        return Listener(SherpaWake(self._spotter), SherpaSegmenter(self._vad_config), SenseVoice(self._recognizer))
