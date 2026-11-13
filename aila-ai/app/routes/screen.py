"""看屏幕找报错：/screen（WebSocket，桌面端开着才看）、/screen/status（设置界面显示）

只有桌面端连着并发了 start 才看；桌面端关了、断开了，马上停。没有「后台偷偷看」这回事。

桌面端发：{"type": "start"} / {"type": "stop"} / {"type": "pause", "seconds": 3600} / {"type": "declined"}
服务端发：{"type": "started", "apps": [...]} / {"type": "error_seen", ...} / {"type": "error", "message": ...}
"""

import asyncio
import contextlib
import json
import logging
import secrets
import sys
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..screen import DECLINE_PAUSE, Finding, ScreenUnavailable, ScreenWatcher, Stats, Window, watched_apps
from ..security import LOCAL_ORIGIN

log = logging.getLogger("aila")
router = APIRouter()

APP_NAMES = {
    "code.exe": "VS Code", "code - insiders.exe": "VS Code", "cursor.exe": "Cursor", "devenv.exe": "Visual Studio",
    "idea64.exe": "IntelliJ IDEA", "pycharm64.exe": "PyCharm", "webstorm64.exe": "WebStorm", "goland64.exe": "GoLand",
    "windowsterminal.exe": "终端", "cmd.exe": "命令提示符", "powershell.exe": "PowerShell", "pwsh.exe": "PowerShell",
    "conhost.exe": "终端", "openconsole.exe": "终端",
}

# 设置界面要显示的：现在有没有在看、最近一次的统计
_state: dict[str, Any] = {"watching": 0, "stats": None}


def app_name(exe: str) -> str:
    return APP_NAMES.get(exe, exe.removesuffix(".exe"))


def make_ocr() -> Any:
    try:
        from ..screen.ocr import Ocr
    except ImportError as err:
        raise ScreenUnavailable("缺少文字识别组件（pip install rapidocr_onnxruntime）") from err
    return Ocr()


def new_watcher() -> ScreenWatcher:
    return ScreenWatcher(make_ocr)


@router.get("/screen/status")
def screen_status() -> dict[str, Any]:
    return {
        "available": sys.platform == "win32",
        "watching": _state["watching"] > 0,
        "apps": sorted(watched_apps()),
        "stats": _state["stats"],
    }


@router.websocket("/screen")
async def screen_ws(ws: WebSocket) -> None:
    origin = ws.headers.get("origin")
    if origin and not LOCAL_ORIGIN.match(origin):
        log.warning("拒绝了来自 %s 的看屏幕连接", origin)
        await ws.close(code=1008)
        return
    await ws.accept()

    watcher = new_watcher()
    stop = asyncio.Event()
    task: asyncio.Task[None] | None = None

    async def emit(finding: Finding, win: Window) -> None:
        await ws.send_json({
            "type": "error_seen", "id": secrets.token_urlsafe(8), "app": app_name(win.app), "title": win.title[:100],
            "rule": finding.rule, "summary": finding.summary, "snippet": finding.snippet,
        })

    async def watch() -> None:
        _state["watching"] += 1
        try:
            await ws.send_json({"type": "started", "apps": sorted(watcher.apps)})
            await watcher.run(emit, stop)
        except ScreenUnavailable as err:
            await ws.send_json({"type": "error", "message": str(err)})
        except Exception as err:  # 截图或识别出了意外：告诉桌面端，别默默停掉
            log.exception("看屏幕出错")
            with contextlib.suppress(Exception):
                await ws.send_json({"type": "error", "message": f"看屏幕出错了（{type(err).__name__}）"})
        finally:
            _state["watching"] -= 1
            _state["stats"] = watcher.stats.summary()

    async def stop_watching() -> None:
        nonlocal task, stop
        if task is None:
            return
        stop.set()
        with contextlib.suppress(Exception):
            await task
        task, stop = None, asyncio.Event()

    try:
        while True:
            cmd = _parse(await ws.receive_text())
            kind = cmd.get("type")
            if kind == "start" and task is None:
                watcher.stats = Stats()
                task = asyncio.create_task(watch())
            elif kind == "stop":
                await stop_watching()
                await ws.send_json({"type": "stopped"})
            elif kind == "pause":
                seconds = cmd.get("seconds")
                watcher.pause(float(seconds) if isinstance(seconds, (int, float)) and 0 < seconds <= 86400 else 3600)
            elif kind == "declined":
                watcher.pause(DECLINE_PAUSE)
            elif kind == "resume":
                watcher.resume()
            elif kind == "stats":
                await ws.send_json({"type": "stats", "stats": watcher.stats.summary()})
    except WebSocketDisconnect:
        pass
    finally:
        await stop_watching()


def _parse(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}
