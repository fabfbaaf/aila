"""她操作安卓手机：通过 ADB（安卓官方的调试工具，USB 线或无线调试）看屏幕、点、打字、滑、按键、打开 App。

- 默认关着：设置 → 手机 里打开「让她操作安卓手机」、选好哪台手机，对话里才有这些工具（tools/android.py）
- ADB 你自己装（Android SDK Platform-Tools），这里只找：AILA_ADB、PATH、Android SDK 的常见位置
- 屏幕用界面树（ui.py），按编号操作，坐标由代码算；会产生后果的点击先问你，执行时再核对一遍
  （问你的时候屏幕可能已经变了：编号对应的元素变了就不点）
- 打中文：ADB 自带的 input text 只认英文；手机上装了开源的 ADBKeyBoard 输入法并切换成它时用它，没装就说清楚
- 命令都是代码拼的固定几种（不把大模型的话当 shell 命令），参数里的文字转义；ADB 的输出只当数据
"""

import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .ui import Screen, UiElement, input_text_arg, parse_screen, tap_needs_confirm, type_problem

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
TIMEOUT = 20.0
UI_FILE = "/sdcard/aila_ui.xml"
ADB_KEYBOARD = "com.android.adbkeyboard"
SETTLE = 0.8  # 操作完等界面变好再看
FRESH = 60.0  # 屏幕清单多久算旧（旧了要先重新看）
SERIAL = re.compile(r"^[\w.:\-]{1,64}$")
ADDRESS = re.compile(r"^[\w.\-]{1,253}:\d{1,5}$")

# 常用 App：她说名字，这里换成包名（别的直接给包名）
APPS = {
    "微信": "com.tencent.mm", "qq": "com.tencent.mobileqq", "支付宝": "com.eg.android.AlipayGphone",
    "淘宝": "com.taobao.taobao", "京东": "com.jingdong.app.mall", "拼多多": "com.xunmeng.pinduoduo",
    "抖音": "com.ss.android.ugc.aweme", "快手": "com.smile.gifmaker", "小红书": "com.xingin.xhs",
    "哔哩哔哩": "tv.danmaku.bili", "b站": "tv.danmaku.bili", "美团": "com.sankuai.meituan",
    "饿了么": "me.ele", "高德地图": "com.autonavi.minimap", "百度地图": "com.baidu.BaiduMap",
    "网易云音乐": "com.netease.cloudmusic", "qq音乐": "com.tencent.qqmusic", "钉钉": "com.alibaba.android.rimet",
    "飞书": "com.ss.android.lark", "企业微信": "com.tencent.wework", "微博": "com.sina.weibo", "知乎": "com.zhihu.android",
    "设置": "com.android.settings", "chrome": "com.android.chrome", "12306": "com.MobileTicket",
}
KEYS = {"back": "KEYCODE_BACK", "home": "KEYCODE_HOME", "recents": "KEYCODE_APP_SWITCH", "enter": "KEYCODE_ENTER"}


class AdbError(Exception):
    """说给用户听的错误"""


def find_adb(env: dict[str, str] | None = None) -> str | None:
    """找 adb：AILA_ADB、PATH、Android SDK 的常见位置"""
    env = env if env is not None else dict(os.environ)
    if (p := env.get("AILA_ADB")) and Path(p).is_file():
        return p
    if found := shutil.which("adb"):
        return found
    home = Path(env.get("USERPROFILE") or env.get("HOME") or Path.home())
    local = Path(env.get("LOCALAPPDATA") or home / "AppData" / "Local")
    bases = [local / "Android" / "Sdk", home / "Android" / "Sdk", home, Path("C:/")]
    bases += [Path(env[k]) for k in ("ANDROID_HOME", "ANDROID_SDK_ROOT") if env.get(k)]
    for base in bases:
        exe = base / "platform-tools" / ("adb.exe" if os.name == "nt" else "adb")
        if exe.is_file():
            return str(exe)
    return None


Runner = Callable[[list[str], float], Awaitable[tuple[int, bytes, bytes]]]


async def run_process(argv: list[str], timeout: float) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                                creationflags=NO_WINDOW)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        raise AdbError("手机好一会儿没反应（ADB 超时）。看看手机是不是锁屏了、线有没有松。") from None
    return proc.returncode or 0, out, err


@dataclass
class Device:
    serial: str
    state: str  # device / unauthorized / offline
    model: str = ""

    def public(self) -> dict[str, str]:
        return {"serial": self.serial, "state": self.state, "model": self.model}


class Adb:
    def __init__(self, path: str, runner: Runner = run_process) -> None:
        self.path = path
        self.runner = runner

    async def run(self, *args: str, serial: str = "", timeout: float = TIMEOUT) -> bytes:
        argv = [self.path, *(["-s", serial] if serial else []), *args]
        code, out, err = await self.runner(argv, timeout)
        if code != 0:
            raise AdbError((err or out).decode("utf-8", "replace").strip()[:300] or f"ADB 出错了（{code}）")
        return out

    async def devices(self) -> list[Device]:
        text = (await self.run("devices", "-l", timeout=10)).decode("utf-8", "replace")
        found = []
        for line in text.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                model = next((p.split(":", 1)[1] for p in parts[2:] if p.startswith("model:")), "")
                found.append(Device(parts[0], parts[1], model.replace("_", " ")))
        return found

    async def pair(self, address: str, code: str) -> str:
        """无线调试配对：手机上「无线调试 → 使用配对码配对」给的地址和 6 位码"""
        if not ADDRESS.match(address) or not re.fullmatch(r"\d{6}", code):
            raise AdbError("地址要写成 IP:端口，配对码是 6 位数字。")
        return (await self.run("pair", address, code, timeout=20)).decode("utf-8", "replace").strip()

    async def connect(self, address: str) -> str:
        if not ADDRESS.match(address):
            raise AdbError("地址要写成 IP:端口（手机上「无线调试」页面最上面那个）。")
        out = (await self.run("connect", address, timeout=15)).decode("utf-8", "replace").strip()
        if "cannot" in out.lower() or "failed" in out.lower():
            raise AdbError(out[:300])
        return out


class AndroidSession:
    """对一台手机的操作；记着上一次看到的屏幕（编号按它算）"""

    def __init__(self, adb: Adb, serial: str, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        if not SERIAL.match(serial):
            raise AdbError("手机的编号不对。")
        self.adb = adb
        self.serial = serial
        self.screen: Screen | None = None
        self.seen_at = 0.0
        self._sleep = sleep

    async def shell(self, *args: str, timeout: float = TIMEOUT) -> bytes:
        return await self.adb.run("shell", *args, serial=self.serial, timeout=timeout)

    async def package(self) -> str:
        out = (await self.shell("dumpsys", "window")).decode("utf-8", "replace")
        m = re.search(r"mCurrentFocus=Window\{[^}]*\s([\w.]+)/", out) or re.search(r"mFocusedApp=.*?\s([\w.]+)/", out)
        return m.group(1) if m else ""

    async def look(self) -> Screen:
        await self.shell("uiautomator", "dump", UI_FILE, timeout=30)
        xml = (await self.adb.run("exec-out", "cat", UI_FILE, serial=self.serial)).decode("utf-8", "replace")
        package = await self.package()
        self.screen = parse_screen(xml, package)
        self.seen_at = time.monotonic()
        return self.screen

    async def read(self) -> str:
        return (await self.look()).describe()

    def element(self, ref: int) -> UiElement | None:
        return self.screen.get(ref) if self.screen else None

    def needs_confirm(self, ref: int) -> bool:
        return tap_needs_confirm(self.element(ref), self.screen.package if self.screen else "")

    def _fresh(self) -> Screen:
        if self.screen is None or time.monotonic() - self.seen_at > FRESH:
            raise AdbError("屏幕清单旧了：先 phone_read 看一下现在的屏幕，再按新的编号操作。")
        return self.screen

    async def tap(self, ref: int, approved: bool = False) -> str:
        screen = self._fresh()
        el = screen.get(ref)
        if el is None:
            raise AdbError("编号对不上：先 phone_read 看一下现在的屏幕。")
        if tap_needs_confirm(el, screen.package) and not approved:
            raise AdbError("点这个要先问用户。")
        if approved:
            # 问你的这段时间屏幕可能变了：重新看一眼，同一个编号还是同一个东西才点
            now = await self.look()
            again = now.get(ref)
            if again is None or again.label != el.label or now.package != screen.package:
                raise AdbError("等你同意的时候屏幕变了，没点。重新看一下再说。")
            el = again
        x, y = el.center
        await self.shell("input", "tap", str(x), str(y))
        return await self._after(f"点了「{el.label or '这个'}」。")

    async def type(self, ref: int, text: str, enter: bool = False) -> str:
        screen = self._fresh()
        el = screen.get(ref)
        if problem := type_problem(el, text):
            raise AdbError(problem)
        assert el is not None
        x, y = el.center
        await self.shell("input", "tap", str(x), str(y))  # 先点一下输入框，让它拿到焦点
        await self._sleep(0.3)
        arg = input_text_arg(text)
        if arg is not None:
            await self.shell("input", "text", arg)
        else:
            if not await self._has_keyboard():
                raise AdbError("ADB 自带的输入只能打英文。要打中文：在手机上装开源的 ADBKeyBoard 输入法并切换成它，再试。")
            payload = base64.b64encode(text.encode("utf-8")).decode("ascii")
            await self.shell("am", "broadcast", "-a", "ADB_INPUT_B64", "--es", "msg", payload)
        if enter:
            await self.shell("input", "keyevent", "KEYCODE_ENTER")
        return await self._after(f"在「{el.label or '输入框'}」里输入了。")

    async def _has_keyboard(self) -> bool:
        out = (await self.shell("settings", "get", "secure", "default_input_method")).decode("utf-8", "replace")
        return out.strip().startswith(ADB_KEYBOARD)

    async def swipe(self, direction: str) -> str:
        size = (await self.shell("wm", "size")).decode("utf-8", "replace")
        m = re.findall(r"(\d+)x(\d+)", size)
        w, h = (int(m[-1][0]), int(m[-1][1])) if m else (1080, 2340)
        cx, cy = w // 2, h // 2
        # direction 是想看哪边的内容：down = 看下面的（手指从下往上滑），up = 看上面的（手指从上往下滑）
        dx, dy = {"up": (0, -h // 3), "down": (0, h // 3), "left": (-w // 3, 0), "right": (w // 3, 0)}.get(direction, (0, h // 3))
        await self.shell("input", "swipe", str(cx + dx), str(cy + dy), str(cx - dx), str(cy - dy), "300")
        return await self._after("翻好了。")

    async def key(self, name: str) -> str:
        code = KEYS.get(name)
        if code is None:
            raise AdbError(f"只能按这些键：{'、'.join(KEYS)}。")
        await self.shell("input", "keyevent", code)
        return await self._after("按好了。")

    async def open_app(self, app: str) -> str:
        pkg = APPS.get(app.strip().lower(), app.strip())
        if not re.fullmatch(r"[A-Za-z][\w]*(\.[\w]+)+", pkg):
            raise AdbError(f"不知道「{app}」是哪个 App：常用的有 {'、'.join(list(APPS)[:12])}……，别的写包名。")
        out = (await self.shell("monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1")).decode("utf-8", "replace")
        if "No activities found" in out or "monkey aborted" in out.lower():
            raise AdbError(f"手机上好像没装 {app}（{pkg}）。")
        return await self._after(f"打开了 {app}。")

    async def _after(self, done: str) -> str:
        await self._sleep(SETTLE)
        return f"{done}\n\n{await self.read()}"


def settings_path(data_dir: Path) -> Path:
    return data_dir / "android.json"


@dataclass
class AndroidSettings:
    """开关和选的哪台手机（data/android.json）"""

    path: Path
    enabled: bool = False
    serial: str = ""

    @staticmethod
    def load(path: Path) -> "AndroidSettings":
        s = AndroidSettings(path)
        try:
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            s.enabled = bool(data.get("enabled"))
            serial = str(data.get("serial", ""))
            s.serial = serial if SERIAL.match(serial) else ""
        except (FileNotFoundError, ValueError):
            pass
        return s

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"enabled": self.enabled, "serial": self.serial}, ensure_ascii=False), encoding="utf-8")
