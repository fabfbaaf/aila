"""她操作安卓手机：界面树解析、哪些点击要先问、密码框不填、ADB 命令怎么拼、同意以后屏幕变了就不点、设置接口。
不连真手机：ADB 换成假的，记下每条命令"""

import base64
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import deps
from app.android import Adb, AdbError, AndroidSession, find_adb
from app.android.ui import input_text_arg, parse_screen, tap_needs_confirm, type_problem
from app.main import app
from app.tools import APPROVED
from app.tools.android import android_skill

XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node class="android.widget.FrameLayout" package="com.tencent.mm" bounds="[0,0][1080,2340]" clickable="false" text="" content-desc="">
    <node class="android.widget.TextView" text="张三" bounds="[200,100][600,180]" clickable="false" package="com.tencent.mm"/>
    <node class="android.widget.LinearLayout" clickable="true" bounds="[0,300][1080,420]" package="com.tencent.mm" resource-id="com.tencent.mm:id/item">
      <node class="android.widget.TextView" text="文件传输助手" bounds="[200,320][800,400]" clickable="false"/>
    </node>
    <node class="android.widget.EditText" text="" hint="说点什么" bounds="[0,2100][900,2200]" clickable="true" package="com.tencent.mm"/>
    <node class="android.widget.Button" text="发送" bounds="[900,2100][1080,2200]" clickable="true" package="com.tencent.mm"/>
    <node class="android.widget.EditText" text="" password="true" bounds="[0,500][1080,600]" clickable="true" package="com.tencent.mm"/>
    <node class="androidx.recyclerview.widget.RecyclerView" scrollable="true" bounds="[0,600][1080,2000]" package="com.tencent.mm"/>
    <node class="android.widget.Button" text="隐藏的" bounds="[0,0][0,0]" clickable="true"/>
  </node>
</hierarchy>"""


def test_parse_screen() -> None:
    s = parse_screen(XML, "com.tencent.mm")
    kinds = [(e.ref, e.kind, e.label) for e in s.elements]
    assert kinds == [(1, "button", "文件传输助手"), (2, "input", "说点什么"), (3, "button", "发送"), (4, "input", ""), (5, "scroll", "")]
    assert s.get(1).center == (540, 360)
    assert s.get(4).secret and not s.get(2).secret
    assert "张三" in s.texts and "文件传输助手" not in s.texts  # 已经算进可点的那块了
    text = s.describe()
    assert "com.tencent.mm" in text and "[3] 按钮「发送」" in text and "（密码框，不能填）" in text
    assert parse_screen("不是 XML").elements == ()


def test_risk_rules() -> None:
    s = parse_screen(XML, "com.tencent.mm")
    assert tap_needs_confirm(s.get(3), "com.tencent.mm")  # 发送
    assert not tap_needs_confirm(s.get(1), "com.tencent.mm")  # 打开一个聊天
    assert tap_needs_confirm(s.get(1), "com.eg.android.AlipayGphone")  # 支付宝里点什么都问
    assert tap_needs_confirm(None, "com.tencent.mm")
    assert type_problem(s.get(4), "123456") and "密码" in type_problem(s.get(4), "x")
    assert type_problem(s.get(3), "x") and type_problem(s.get(2), "你好") is None
    assert input_text_arg("hello world") == "hello%sworld"
    assert input_text_arg("a;rm -rf") == "a\\;rm%s-rf"
    assert input_text_arg("你好") is None and input_text_arg("") is None


class FakeAdb:
    """假 ADB：按子命令给固定的输出，记下每条命令"""

    def __init__(self, xml: str = XML, keyboard: bool = False, package: str = "com.tencent.mm") -> None:
        self.calls: list[list[str]] = []
        self.xml = xml
        self.keyboard = keyboard
        self.package = package

    async def __call__(self, argv: list[str], timeout: float) -> tuple[int, bytes, bytes]:
        args = argv[3:] if argv[1] == "-s" else argv[1:]
        self.calls.append(args)
        if args[:2] == ["exec-out", "cat"]:
            return 0, self.xml.encode(), b""
        if args[:2] == ["shell", "dumpsys"]:
            return 0, f"  mCurrentFocus=Window{{abc u0 {self.package}/.ui.LauncherUI}}".encode(), b""
        if args[:2] == ["shell", "settings"]:
            return 0, (b"com.android.adbkeyboard/.AdbIME" if self.keyboard else b"com.sohu.inputmethod/.Ime"), b""
        if args[:2] == ["shell", "wm"]:
            return 0, b"Physical size: 1080x2340", b""
        if args[:1] == ["devices"]:
            return 0, b"List of devices attached\nABC123 device product:x model:Pixel_7 device:y\n10.0.0.5:5555 unauthorized\n", b""
        return 0, b"", b""


async def no_sleep(_: float) -> None:
    return None


def session(fake: FakeAdb) -> AndroidSession:
    return AndroidSession(Adb("adb", fake), "ABC123", sleep=no_sleep)


def taps(fake: FakeAdb) -> list[list[str]]:
    return [c for c in fake.calls if c[:3] == ["shell", "input", "tap"]]


@pytest.mark.anyio
async def test_read_and_tap() -> None:
    fake = FakeAdb()
    s = session(fake)
    text = await s.read()
    assert "[1] 按钮「文件传输助手」" in text
    assert ["shell", "uiautomator", "dump", "/sdcard/aila_ui.xml"] in fake.calls
    out = await s.tap(1)
    assert taps(fake) == [["shell", "input", "tap", "540", "360"]] and "点了「文件传输助手」" in out
    # 发送：没同意不点
    with pytest.raises(AdbError, match="先问"):
        await s.tap(3)
    # 同意了、屏幕没变：点
    await s.tap(3, approved=True)
    assert taps(fake)[-1] == ["shell", "input", "tap", "990", "2150"]
    # 同意了但屏幕变了（同一个编号成了别的东西）：不点
    fake.xml = XML.replace('text="发送"', 'text="转账"')
    await s.read()
    fake.xml = XML.replace('text="发送"', 'text="删除"')
    n = len(taps(fake))
    with pytest.raises(AdbError, match="屏幕变了"):
        await s.tap(3, approved=True)
    assert len(taps(fake)) == n
    with pytest.raises(AdbError, match="编号对不上"):
        await s.tap(99)


@pytest.mark.anyio
async def test_stale_screen_must_be_reread(monkeypatch: pytest.MonkeyPatch) -> None:
    s = session(FakeAdb())
    with pytest.raises(AdbError, match="旧了"):
        await s.tap(1)
    await s.read()
    s.seen_at -= 3600
    with pytest.raises(AdbError, match="旧了"):
        await s.tap(1)


@pytest.mark.anyio
async def test_typing() -> None:
    fake = FakeAdb()
    s = session(fake)
    await s.read()
    await s.type(2, "hi there", enter=True)
    assert ["shell", "input", "text", "hi%sthere"] in fake.calls and ["shell", "input", "keyevent", "KEYCODE_ENTER"] in fake.calls
    with pytest.raises(AdbError, match="ADBKeyBoard"):
        await s.type(2, "你好")
    fake.keyboard = True
    await s.type(2, "你好")
    payload = base64.b64encode("你好".encode()).decode()
    assert ["shell", "am", "broadcast", "-a", "ADB_INPUT_B64", "--es", "msg", payload] in fake.calls
    with pytest.raises(AdbError, match="密码"):
        await s.type(4, "123456")


@pytest.mark.anyio
async def test_keys_swipe_and_apps() -> None:
    fake = FakeAdb()
    s = session(fake)
    await s.key("back")
    assert ["shell", "input", "keyevent", "KEYCODE_BACK"] in fake.calls
    with pytest.raises(AdbError):
        await s.key("power")
    await s.swipe("down")  # 看下面的：手指从下往上
    swipe = next(c for c in fake.calls if c[:3] == ["shell", "input", "swipe"])
    assert int(swipe[4]) > int(swipe[6])
    await s.open_app("微信")
    assert any(c[:4] == ["shell", "monkey", "-p", "com.tencent.mm"] for c in fake.calls)
    with pytest.raises(AdbError, match="不知道"):
        await s.open_app("rm -rf /")
    with pytest.raises(AdbError):
        AndroidSession(Adb("adb", fake), "a b; reboot")


@pytest.mark.anyio
async def test_devices_and_pairing_args() -> None:
    fake = FakeAdb()
    adb = Adb("adb", fake)
    devices = await adb.devices()
    assert [(d.serial, d.state, d.model) for d in devices] == [("ABC123", "device", "Pixel 7"), ("10.0.0.5:5555", "unauthorized", "")]
    with pytest.raises(AdbError):
        await adb.pair("10.0.0.5:37000; reboot", "123456")
    with pytest.raises(AdbError):
        await adb.connect("not an address")
    await adb.pair("10.0.0.5:37000", "123456")
    assert ["pair", "10.0.0.5:37000", "123456"] in fake.calls


def test_find_adb(tmp_path: Path) -> None:
    exe = tmp_path / "adb.exe"
    exe.write_bytes(b"")
    assert find_adb({"AILA_ADB": str(exe), "PATH": ""}) == str(exe)


@pytest.mark.anyio
async def test_skill_asks_before_risky_taps() -> None:
    fake = FakeAdb()
    s = session(fake)
    await s.read()
    skill = android_skill(lambda: s, lambda: True)
    tap = next(t for t in skill.tools if t.name == "phone_tap")
    assert not tap.requires_confirmation({"ref": 1})
    assert tap.requires_confirmation({"ref": 3})
    assert tap.action({"ref": 3}) == "在手机上点「发送」"
    assert "所在 App：com.tencent.mm" in tap.detail({"ref": 3})
    out = await tap.run({"ref": 1})
    assert "以下内容来自外部" in out  # 屏幕内容只当数据
    token = APPROVED.set(True)
    try:
        await tap.run({"ref": 3})
    finally:
        APPROVED.reset(token)
    assert taps(fake)[-1][-2:] == ["990", "2150"]


def test_settings_api(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(app)
    monkeypatch.setattr(deps, "get_adb", lambda: None)
    status = client.get("/android").json()
    assert status == {"enabled": False, "serial": "", "adb": None, "devices": [], "error": ""}
    assert not deps.android_ready()
    assert client.post("/android/pair", json={"address": "10.0.0.5:37000", "code": "123456"}).status_code == 409
    assert client.put("/android", json={"serial": "a b"}).status_code == 400
    status = client.put("/android", json={"enabled": True, "serial": "ABC123"}).json()
    assert status["enabled"] and status["serial"] == "ABC123"
    fake: Any = FakeAdb()
    monkeypatch.setattr(deps, "get_adb", lambda: Adb("adb", fake))
    assert [d["serial"] for d in client.get("/android").json()["devices"]] == ["ABC123", "10.0.0.5:5555"]
    assert client.put("/android", json={"enabled": True}, headers={"Origin": "https://evil.example"}).status_code == 403
