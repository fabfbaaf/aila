"""看屏幕找报错：一层层过滤，越往后越贵、越少走到。

  第 0 层  前台不是写代码的程序 → 不截图（隐私，也最省）
  第 1 层  画面和上次识别时一样 → 跳过；还在变（滚动、输出中）→ 等它停下再看
  第 2 层  只识别变了的那几行（本地 OCR）
  第 3 层  规则判断是不是报错；同一个错误只提醒一次
  第 4 层  交给桌面端问用户「要我帮你看看吗？」，同意了才把那段报错交给大模型

每层过滤掉多少都有计数（stats），用来量化「绝大多数截图走不到大模型」。
截图和识别出的文字只在内存里，日志只记数字。
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

import numpy as np

from .capture import Grabber, Window, foreground, watched_apps
from .detect import Finding, detect

log = logging.getLogger("aila.screen")

INTERVAL = 2.0  # 每 2 秒看一眼
CELL = 16  # 比较画面时，16×16 像素一格
CELL_DIFF = 10  # 一格的平均灰度变化超过这么多才算变了（光标闪烁不算）
MIN_CHANGED_CELLS = 3
CONTEXT_ROWS = 4  # 识别时往上多带几行格子，免得 Traceback 的开头被切掉
DEDUPE_SECONDS = 1800.0  # 同一个错误多久之内不再提醒
DECLINE_PAUSE = 600.0  # 用户说「不用」之后安静一会儿


class OcrEngine(Protocol):
    def lines(self, image: np.ndarray) -> list[str]: ...


Capture = Callable[[], tuple[Window, np.ndarray] | None]
Emit = Callable[[Finding, Window], Awaitable[None]]


@dataclass
class Stats:
    ticks: int = 0  # 看了几次（每 2 秒一次）
    other_app: int = 0  # 前台不是写代码的程序，没截图
    unchanged: int = 0  # 画面没变
    unsettled: int = 0  # 画面还在变，等下一次
    ocr: int = 0  # 真正识别了几次
    ocr_ms: float = 0.0
    errors: int = 0  # 认出是报错
    duplicates: int = 0  # 同一个错误已经问过 / 用户说了先别打扰
    asked: int = 0  # 交给桌面端去问用户
    started_at: float = field(default_factory=time.time)

    def summary(self) -> dict[str, Any]:
        d = asdict(self)
        d["ocr_avg_ms"] = round(self.ocr_ms / self.ocr) if self.ocr else 0
        d["ocr_ms"] = round(self.ocr_ms)
        return d


def thumbnail(image: np.ndarray) -> np.ndarray:
    """每 16×16 像素取平均灰度，用来比较画面变没变（便宜：先隔 4 个像素取一个）"""
    h, w = image.shape[0] // CELL * CELL, image.shape[1] // CELL * CELL
    gray = image[:h:4, :w:4].mean(axis=2)
    step = CELL // 4
    return gray.reshape(h // CELL, step, w // CELL, step).mean(axis=(1, 3))


def changed_cells(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    if a.shape != b.shape:
        return np.ones(a.shape, dtype=bool)
    return np.abs(a - b) > CELL_DIFF


def changed_rows(a: np.ndarray, b: np.ndarray, height: int) -> tuple[int, int] | None:
    """变了的那几行（像素）。文字是一整行一整行的，所以按行裁、不按列裁"""
    cells = changed_cells(a, b)
    if cells.sum() < MIN_CHANGED_CELLS:
        return None
    rows = np.nonzero(cells.any(axis=1))[0]
    top = max(0, int(rows[0]) - CONTEXT_ROWS) * CELL
    bottom = min(height, (int(rows[-1]) + 2) * CELL)
    return top, bottom


class ScreenWatcher:
    def __init__(
        self,
        ocr_factory: Callable[[], OcrEngine],
        capture: Capture | None = None,
        apps: frozenset[str] | None = None,
        interval: float = INTERVAL,
    ) -> None:
        self._ocr_factory = ocr_factory
        self._ocr: OcrEngine | None = None
        self._apps = apps if apps is not None else watched_apps()
        self._capture = capture or self._capture_foreground
        self._grabber = Grabber()
        self._interval = interval
        self.stats = Stats()
        self._asked: dict[str, float] = {}
        self._paused_until = 0.0
        self._window: tuple[int, tuple[int, ...]] | None = None
        self._prev: np.ndarray | None = None  # 上一次看到的
        self._seen: np.ndarray | None = None  # 上一次识别时的

    @property
    def apps(self) -> frozenset[str]:
        return self._apps

    def pause(self, seconds: float) -> None:
        self._paused_until = max(self._paused_until, time.monotonic() + seconds)

    def resume(self) -> None:
        self._paused_until = 0.0

    @property
    def paused(self) -> bool:
        return time.monotonic() < self._paused_until

    def _capture_foreground(self) -> tuple[Window, np.ndarray] | None:
        win = foreground()
        if win is None or win.app not in self._apps:
            return None  # 不是写代码的程序：不截图
        image = self._grabber.grab(win.rect)
        return (win, image) if image is not None else None

    async def run(self, emit: Emit, stop: asyncio.Event) -> None:
        """截图和识别都放在同一个后台线程里（截图组件不能跨线程，识别要占 CPU）"""
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="aila-screen") as pool:
            self._ocr = await loop.run_in_executor(pool, self._ocr_factory)
            log.info("开始看屏幕（只看 %d 种写代码的程序）", len(self._apps))
            while not stop.is_set():
                started = time.monotonic()
                await self.tick(lambda fn, *a: loop.run_in_executor(pool, fn, *a), emit)
                wait = max(0.1, self._interval - (time.monotonic() - started))
                try:
                    await asyncio.wait_for(stop.wait(), wait)
                except TimeoutError:
                    pass
            log.info("不看屏幕了：%s", self.stats.summary())

    async def tick(self, run: Callable[..., Awaitable[Any]], emit: Emit) -> None:
        if self.paused:
            return
        self.stats.ticks += 1
        shot = await run(self._capture)
        if shot is None:
            self.stats.other_app += 1
            self._prev = None
            return
        win, image = shot
        thumb = thumbnail(image)
        key = (win.hwnd, image.shape)
        if key != self._window:  # 换了窗口（或者窗口大小变了）
            self._window, self._prev, self._seen = key, None, None

        changed = self._seen is None or changed_cells(thumb, self._seen).sum() >= MIN_CHANGED_CELLS
        settled = self._prev is not None and changed_cells(thumb, self._prev).sum() < MIN_CHANGED_CELLS
        self._prev = thumb
        if not changed:
            self.stats.unchanged += 1
            return
        if not settled:
            self.stats.unsettled += 1
            return

        rows = changed_rows(thumb, self._seen, image.shape[0]) if self._seen is not None else (0, image.shape[0])
        crop = image[rows[0]:rows[1]] if rows else image
        t0 = time.perf_counter()
        assert self._ocr is not None
        lines = await run(self._ocr.lines, np.ascontiguousarray(crop))
        self.stats.ocr += 1
        self.stats.ocr_ms += (time.perf_counter() - t0) * 1000
        self._seen = thumb

        finding = detect(lines)
        if finding is None:
            return
        self.stats.errors += 1
        now = time.monotonic()
        if now - self._asked.get(finding.signature, -DEDUPE_SECONDS - 1) < DEDUPE_SECONDS:
            self.stats.duplicates += 1
            return
        self._asked[finding.signature] = now
        self.stats.asked += 1
        log.info("看到报错（%s，%s）", finding.rule, win.app)
        await emit(finding, win)
