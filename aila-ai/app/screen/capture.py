"""前台窗口截图（Windows）。

隐私：先看前台窗口是哪个程序，只有在白名单里（写代码的程序：编辑器、IDE、终端）才截图，
别的窗口（浏览器、聊天、银行、密码管理器）连像素都不读。截图只在内存里，不写硬盘。
"""

import ctypes
import os
import sys
import threading
from ctypes import wintypes
from dataclasses import dataclass

import numpy as np

# 写代码时常用的程序（进程名，小写）。可以用 AILA_SCREEN_APPS 追加，逗号分隔
DEV_APPS = frozenset({
    # 编辑器、IDE
    "code.exe", "code - insiders.exe", "cursor.exe", "windsurf.exe", "trae.exe", "zed.exe", "devenv.exe",
    "idea64.exe", "pycharm64.exe", "webstorm64.exe", "goland64.exe", "clion64.exe", "rider64.exe",
    "phpstorm64.exe", "rubymine64.exe", "datagrip64.exe", "studio64.exe", "fleet.exe",
    "sublime_text.exe", "notepad++.exe",
    # 终端
    "windowsterminal.exe", "cmd.exe", "powershell.exe", "pwsh.exe", "conhost.exe", "openconsole.exe",
    "wezterm-gui.exe", "alacritty.exe", "mintty.exe", "tabby.exe", "hyper.exe", "warp.exe",
})

MIN_SIZE = 120  # 太小的窗口（工具提示、托盘弹窗）不看


def watched_apps() -> frozenset[str]:
    extra = {a.strip().lower() for a in os.getenv("AILA_SCREEN_APPS", "").split(",") if a.strip()}
    return DEV_APPS | extra


@dataclass(frozen=True)
class Window:
    hwnd: int
    app: str  # 进程名，小写，比如 code.exe
    title: str
    rect: tuple[int, int, int, int]  # 左、上、右、下（物理像素）


class ScreenUnavailable(Exception):
    """这台电脑上看不了屏幕（不是 Windows，或者缺依赖）"""


if sys.platform == "win32":
    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32
    _dwmapi = ctypes.windll.dwmapi
    _dpi_once = threading.Lock()
    _dpi_done = False

    def _dpi_aware() -> None:
        """按物理像素取窗口位置，否则高分屏上截到的位置会偏"""
        global _dpi_done
        with _dpi_once:
            if _dpi_done:
                return
            _dpi_done = True
            try:
                _user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # 每个显示器单独缩放 v2
            except Exception:
                try:
                    ctypes.windll.shcore.SetProcessDpiAwareness(2)
                except Exception:
                    pass

    def _process_name(hwnd: int) -> str:
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = _kernel32.OpenProcess(0x1000, False, pid.value)  # 只查询有限信息，不需要管理员权限
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(1024)
            buf = ctypes.create_unicode_buffer(size.value)
            if not _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return ""
            return os.path.basename(buf.value).lower()
        finally:
            _kernel32.CloseHandle(handle)

    def foreground() -> Window | None:
        _dpi_aware()
        hwnd = _user32.GetForegroundWindow()
        if not hwnd or _user32.IsIconic(hwnd):
            return None
        rect = wintypes.RECT()
        # 去掉阴影的真实边框；拿不到就用普通窗口矩形
        if _dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)) != 0:
            _user32.GetWindowRect(hwnd, ctypes.byref(rect))
        if rect.right - rect.left < MIN_SIZE or rect.bottom - rect.top < MIN_SIZE:
            return None
        length = _user32.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, title, length + 1)
        return Window(int(hwnd), _process_name(hwnd), title.value, (rect.left, rect.top, rect.right, rect.bottom))

else:

    def foreground() -> Window | None:
        raise ScreenUnavailable("看屏幕现在只支持 Windows")


class Grabber:
    """截一块屏幕区域，返回 BGR 图像。mss 不能跨线程用，所以每个线程一个"""

    def __init__(self) -> None:
        self._local = threading.local()

    def grab(self, rect: tuple[int, int, int, int]) -> np.ndarray | None:
        try:
            import mss
        except ImportError as err:
            raise ScreenUnavailable("缺少截图组件（pip install mss）") from err
        sct = getattr(self._local, "sct", None)
        if sct is None:
            sct = self._local.sct = mss.mss()
        screen = sct.monitors[0]  # 所有显示器拼起来的范围
        left = max(rect[0], screen["left"])
        top = max(rect[1], screen["top"])
        right = min(rect[2], screen["left"] + screen["width"])
        bottom = min(rect[3], screen["top"] + screen["height"])
        if right - left < MIN_SIZE or bottom - top < MIN_SIZE:
            return None
        shot = sct.grab({"left": left, "top": top, "width": right - left, "height": bottom - top})
        return np.frombuffer(shot.bgra, dtype=np.uint8).reshape(shot.height, shot.width, 4)[:, :, :3]
