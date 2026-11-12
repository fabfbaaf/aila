"""音频格式转换：桌面端发来 16kHz 单声道 16 位 PCM，模型要 float32，合成结果打包成 WAV"""

import io
import wave

import numpy as np

SAMPLE_RATE = 16000


def pcm16_to_float(data: bytes) -> np.ndarray:
    if len(data) % 2:
        data = data[:-1]  # 半个采样点是坏数据，丢掉
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


def float_to_wav(samples: np.ndarray, sample_rate: int) -> bytes:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()
