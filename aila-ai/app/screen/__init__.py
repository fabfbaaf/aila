"""看屏幕找报错：前台窗口截图（只看写代码的程序）→ 画面变化检测 → 本地 OCR → 规则判断"""

from .capture import DEV_APPS, ScreenUnavailable, Window, watched_apps
from .detect import Finding, detect, redact
from .watcher import DECLINE_PAUSE, ScreenWatcher, Stats

__all__ = [
    "DECLINE_PAUSE", "DEV_APPS", "Finding", "ScreenUnavailable", "ScreenWatcher", "Stats", "Window",
    "detect", "redact", "watched_apps",
]
