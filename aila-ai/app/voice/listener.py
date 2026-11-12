"""艾拉的「耳朵」：一条连接一个 Listener，喂麦克风音频，吐出事件。

两种模式：
- wake：只跑唤醒词识别，听到「你好艾拉」切到 listen。声音只在本机处理。
- listen：断句（VAD）+ 语音识别。每说完一段，先发 speech_end，再发 transcript 或 empty。

什么时候回到 wake 由桌面端决定（它知道这次对话是否结束），这里只管听。
模型都通过接口传进来，测试时可以换成假的。
"""

import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Literal, Protocol

import numpy as np

from .audio import SAMPLE_RATE

Mode = Literal["off", "wake", "listen"]
Event = dict[str, object]

# 唤醒后把前面这么多秒的音频也交给断句：「你好艾拉，明天天气怎么样」常常是一口气说完的
PREROLL_SECONDS = 2.0
# 识别结果里去掉唤醒词（识别模型可能写成「爱拉」「艾啦」）
WAKE_PREFIX = re.compile(r"^.*?你好[\s，,、。！!]*[艾爱][拉啦][\s，,、。！!?？]*")
# 只有标点、语气词时当作没听清
FILLERS = re.compile(r"^[\s，,、。！!？?…~～嗯啊哦呃额唔]*$")


@dataclass
class Transcript:
    text: str
    lang: str = ""
    emotion: str = ""
    event: str = ""


class WakeDetector(Protocol):
    def accept(self, samples: np.ndarray) -> str | None:
        """喂一段音频，听到唤醒词时返回它"""
        ...

    def reset(self) -> None: ...


class Segmenter(Protocol):
    """断句（VAD）"""

    def accept(self, samples: np.ndarray) -> tuple[bool, list[np.ndarray]]:
        """返回（这段音频里是否刚开始说话，已经说完的整段音频）"""
        ...

    @property
    def speaking(self) -> bool: ...

    def reset(self) -> None: ...


class Transcriber(Protocol):
    def transcribe(self, samples: np.ndarray) -> Transcript: ...


class Listener:
    def __init__(self, wake: WakeDetector, segmenter: Segmenter, transcriber: Transcriber) -> None:
        self._wake = wake
        self._seg = segmenter
        self._asr = transcriber
        self.mode: Mode = "wake"
        self._preroll: deque[np.ndarray] = deque()
        self._preroll_len = 0
        self._in_speech = False
        self._after_wake = False

    def set_mode(self, mode: Mode) -> None:
        if mode == self.mode:
            return
        self.mode = mode
        self._wake.reset()
        self._seg.reset()
        self._preroll.clear()
        self._preroll_len = 0
        self._in_speech = False
        self._after_wake = False

    def feed(self, samples: np.ndarray) -> list[Event]:
        """喂一小段音频（16kHz float32），返回这段音频产生的事件。会阻塞，调用方放到线程里跑。"""
        if self.mode == "off" or len(samples) == 0:
            return []
        if self.mode == "listen":
            return self._listen(samples)

        self._remember(samples)
        keyword = self._wake.accept(samples)
        if not keyword:
            return []
        preroll = np.concatenate(list(self._preroll))
        self.set_mode("listen")
        self._after_wake = True
        return [{"type": "wake", "keyword": keyword}, *self._listen(preroll)]

    def _remember(self, samples: np.ndarray) -> None:
        self._preroll.append(samples)
        self._preroll_len += len(samples)
        while self._preroll_len - len(self._preroll[0]) >= PREROLL_SECONDS * SAMPLE_RATE:
            self._preroll_len -= len(self._preroll.popleft())

    def _listen(self, samples: np.ndarray) -> list[Event]:
        events: list[Event] = []
        started, segments = self._seg.accept(samples)
        if started and not self._in_speech:
            self._in_speech = True
            events.append({"type": "speech_start"})
        for seg in segments:
            self._in_speech = self._seg.speaking
            events.append({"type": "speech_end", "seconds": round(len(seg) / SAMPLE_RATE, 2)})
            events.append(self._recognize(seg))
            self._after_wake = False
        return events

    def _recognize(self, seg: np.ndarray) -> Event:
        t0 = time.perf_counter()
        result = self._asr.transcribe(seg)
        ms = round((time.perf_counter() - t0) * 1000)
        text = result.text.strip()
        if self._after_wake:
            text = WAKE_PREFIX.sub("", text)
        if FILLERS.match(text):
            return {"type": "empty", "asr_ms": ms}
        return {"type": "transcript", "text": text, "emotion": result.emotion, "asr_ms": ms}
