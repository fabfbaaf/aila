"""艾拉的「嗓子」：文字转语音。桌面端按句请求，这里一次合成一句。

三种本地引擎，在 .env 里用 AILA_TTS_ENGINE 选（在 CPU 上实测，一句 10 个字左右）：
- melo：中英混读，约 0.3 秒，MIT 许可（默认）
- matcha：只会中文（英文单词会被跳过），约 0.1 秒；训练数据标贝只许非商业使用
- kokoro：中英混读、上百种音色，但最慢（CPU 上实测一句 10 字约 1.3 秒，合成用时约是念出来时长的一半），有显卡时更合适
"""

import re
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import sherpa_onnx as so


@dataclass(frozen=True)
class TtsConfig:
    models_dir: Path
    engine: str = "melo"
    speaker: int = 0
    speed: float = 1.0
    threads: int = 4
    # MeloTTS（VITS）合成时带随机噪声，同一句话每次念得略有不同，更自然。测试时设为 True 让结果固定
    deterministic: bool = False


def _fsts(d: Path, *names: str) -> str:
    return ",".join(str(d / n) for n in names if (d / n).exists())


def _melo(m: Path, threads: int, deterministic: bool = False) -> so.OfflineTtsConfig:
    d = m / "vits-melo-tts-zh_en"
    noise = {"noise_scale": 0.0, "noise_scale_w": 0.0} if deterministic else {}
    return so.OfflineTtsConfig(
        model=so.OfflineTtsModelConfig(
            vits=so.OfflineTtsVitsModelConfig(
                model=str(d / "model.onnx"),
                lexicon=str(d / "lexicon.txt"),
                tokens=str(d / "tokens.txt"),
                dict_dir=str(d / "dict"),
                **noise,
            ),
            num_threads=threads,
        ),
        rule_fsts=_fsts(d, "phone.fst", "date.fst", "number.fst"),
    )


def _matcha(m: Path, threads: int) -> so.OfflineTtsConfig:
    d = m / "matcha-icefall-zh-baker"
    return so.OfflineTtsConfig(
        model=so.OfflineTtsModelConfig(
            matcha=so.OfflineTtsMatchaModelConfig(
                acoustic_model=str(d / "model-steps-3.onnx"),
                vocoder=str(m / "vocos-22khz-univ.onnx"),
                lexicon=str(d / "lexicon.txt"),
                tokens=str(d / "tokens.txt"),
                dict_dir=str(d / "dict"),
            ),
            num_threads=threads,
        ),
        rule_fsts=_fsts(d, "phone.fst", "date.fst", "number.fst"),
    )


def _kokoro(m: Path, threads: int) -> so.OfflineTtsConfig:
    d = m / "kokoro-multi-lang-v1_1"
    if not d.exists():
        d = m / "kokoro-int8-multi-lang-v1_1"
    model = d / "model.onnx" if (d / "model.onnx").exists() else d / "model.int8.onnx"
    return so.OfflineTtsConfig(
        model=so.OfflineTtsModelConfig(
            kokoro=so.OfflineTtsKokoroModelConfig(
                model=str(model),
                voices=str(d / "voices.bin"),
                tokens=str(d / "tokens.txt"),
                lexicon=f"{d / 'lexicon-us-en.txt'},{d / 'lexicon-zh.txt'}",
                data_dir=str(d / "espeak-ng-data"),
                dict_dir=str(d / "dict"),
            ),
            num_threads=threads,
        ),
        rule_fsts=_fsts(d, "phone-zh.fst", "date-zh.fst", "number-zh.fst"),
    )


ENGINES = {"melo": _melo, "matcha": _matcha, "kokoro": _kokoro}

_URL = re.compile(r"https?://\S+")
_MARKUP = re.compile(r"[*#`_~>|\[\]{}<>「」『』《》【】\"“”]")
_ELLIPSIS = re.compile(r"[…⋯]+|\.{3,}")
_SPEAKABLE = re.compile(r"[\w一-鿿]")


def clean_for_speech(text: str) -> str:
    """去掉念出来很怪的东西：网址、Markdown 符号、表情；省略号换成停顿"""
    text = _URL.sub("", text)
    text = _MARKUP.sub("", text)
    text = "".join(ch for ch in text if ord(ch) < 0x10000)  # emoji 等
    text = _ELLIPSIS.sub("，", text)
    text = re.sub(r"\s+", " ", text)
    text = text.strip(" ，,、；;：:")
    return text if _SPEAKABLE.search(text) else ""


class Voice:
    def __init__(self, cfg: TtsConfig) -> None:
        if cfg.engine not in ENGINES:
            raise ValueError(f"AILA_TTS_ENGINE 只能是 {' / '.join(ENGINES)}")
        self._cfg = cfg
        build = ENGINES[cfg.engine]
        config = _melo(cfg.models_dir, cfg.threads, cfg.deterministic) if build is _melo else build(cfg.models_dir, cfg.threads)
        self._tts = so.OfflineTts(config)
        self.sample_rate: int = self._tts.sample_rate
        # 合成很吃 CPU，一次只合成一句；桌面端会提前请求下一句，排队等着就行
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return self._cfg.engine

    def synthesize(self, text: str, speed: float | None = None) -> np.ndarray | None:
        """返回 float32 采样；没有能念的内容时返回 None"""
        cleaned = clean_for_speech(text)
        if not cleaned:
            return None
        with self._lock:
            audio = self._tts.generate(cleaned, sid=self._cfg.speaker, speed=speed or self._cfg.speed)
        samples = np.asarray(audio.samples, dtype=np.float32)
        return samples if samples.size else None
