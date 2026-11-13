"""看屏幕找报错：规则、打码、分行、漏斗各层的过滤、真实 OCR、WebSocket 接口。
测试里不截用户的屏幕：画面是程序画出来的。"""

import asyncio
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app
from app.routes import screen as screen_route
from app.screen import ScreenWatcher, Window, detect, redact
from app.screen.detect import signature
from app.screen.ocr import group_lines

TRACEBACK = [
    "PS C:\\work\\demo> python app.py",
    "Traceback (most recent call last):",
    'File "C:\\work\\demo\\app.py", line 12, in <module>',
    "main()",
    'File "C:\\work\\demo\\app.py", line 8, in main',
    "total = price * count",
    "TypeError: unsupported operand type(s) for *: 'NoneType' and 'int'",
    "PS C:\\work\\demo>",
]


# ---------- 规则 ----------


def test_python_traceback_summary_is_the_error_line() -> None:
    f = detect(TRACEBACK)
    assert f is not None and f.rule == "python"
    assert f.summary == "TypeError: unsupported operand type(s) for *: 'NoneType' and 'int'"
    assert "Traceback (most recent call last):" in f.snippet and f.summary in f.snippet


@pytest.mark.parametrize(
    ("line", "rule"),
    [
        ("src/main.ts(3,7): error TS2322: Type 'string' is not assignable to type 'number'.", "typescript"),
        ("src/main.ts(3,7): error Ts2322: Type 'string'", "typescript"),  # OCR 把大小写认错
        ("Type 'string' is not assignable to type 'number'. ts(2322) [Ln 3, Col 7]", "typescript"),
        ("npm ERR! code ELIFECYCLE", "npm"),
        ('Exception in thread "main" java.lang.NullPointerException', "java"),
        ("java.lang.IllegalStateException: Failed to load ApplicationContext", "exception"),
        ("[ERROR] Failed to execute goal org.apache.maven.plugins", "maven"),
        ("BUILD FAILED in 3s", "build"),
        ("构建失败，共 1 个错误", "build"),
        ("error[E0382]: borrow of moved value: `v`", "rust"),
        ("panic: runtime error: index out of range [3] with length 3", "go"),
        ("main.c:5:10: fatal error: stdio.h: No such file or directory", "c"),
        ("FAILED tests/test_app.py::test_total - TypeError: x", "test"),
        ("Uncaught (in promise) TypeError: Cannot read properties of undefined", "uncaught"),
        ("KeyError: 'price'", "exception"),
    ],
)
def test_error_rules(line: str, rule: str) -> None:
    f = detect(["$ run", line])
    assert f is not None and f.rule == rule


@pytest.mark.parametrize(
    "line",
    [
        'raise ValueError("bad price")',  # 编辑器里的代码
        "except KeyError:",
        "class PriceError(Exception):",
        "def handle_error(err):",
        "// TODO: better error handling",
        "0 failed, 12 passed",  # 测试全过
        "Errors: 0",
    ],
)
def test_code_and_normal_output_are_not_errors(line: str) -> None:
    assert detect(["x = 1", line, "y = 2"]) is None


def test_same_error_with_other_line_numbers_is_the_same() -> None:
    assert signature("TypeError at line 12: 'a'") == signature("TypeError at line 40: 'b'")
    assert signature("TypeError: x") != signature("ValueError: x")


def test_secrets_are_masked() -> None:
    text = redact(
        "openai.AuthenticationError: Incorrect API key provided: sk-abcdef1234567890abcd\n"
        "api_key = 'live_123'  token: abc.def\n"
        "Authorization: Bearer eyJhbGciOi.payload.sig\n"
        "aws 9f8e7d6c5b4a39281706f5e4d3c2b1a0aabbccdd\n"
        "C:\\Users\\me\\very_long_project_directory_name\\src\\main.py\n"
        "test_agent_asks_before_clicking_and_drops_old_pages failed"
    )
    assert "sk-abcdef" not in text and "sk-***" in text
    assert "live_123" not in text and "abc.def" not in text
    assert "eyJhbGciOi" not in text
    assert "9f8e7d6c" not in text
    assert "very_long_project_directory_name" in text  # 路径保留
    assert "test_agent_asks_before_clicking_and_drops_old_pages" in text  # 纯字母的长名字保留


def test_boxes_become_lines() -> None:
    def box(x: float, y: float, text: str, w: float = 50, h: float = 20) -> Any:
        return [[[x, y], [x + w, y], [x + w, y + h], [x, y + h]], text, 0.9]

    result = [box(200, 52, "world"), box(10, 10, "first"), box(10, 50, "hello"), box(80, 11, "line")]
    assert group_lines(result) == ["first line", "hello world"]


# ---------- 漏斗 ----------


class FakeScreen:
    """按顺序给出画面；画面是程序画的灰度块"""

    def __init__(self) -> None:
        self.app = "code.exe"
        self.image = np.full((480, 640, 3), 30, dtype=np.uint8)

    def draw(self, top: int, bottom: int, value: int = 220) -> None:
        self.image = self.image.copy()
        self.image[top:bottom, 20:600] = value

    def capture(self) -> tuple[Window, np.ndarray] | None:
        if self.app != "code.exe":
            return None
        return Window(1, self.app, "app.py - demo - Visual Studio Code", (0, 0, 640, 480)), self.image


class FakeOcr:
    def __init__(self) -> None:
        self.next: list[str] = []
        self.crops: list[tuple[int, ...]] = []

    def lines(self, image: np.ndarray) -> list[str]:
        self.crops.append(image.shape)
        return self.next


async def direct(fn: Any, *args: Any) -> Any:
    return fn(*args)


@pytest.mark.anyio
async def test_funnel_skips_cheaply_and_asks_once() -> None:
    screen, ocr = FakeScreen(), FakeOcr()
    w = ScreenWatcher(lambda: ocr, capture=screen.capture, apps=frozenset({"code.exe"}))
    w._ocr = ocr
    asked: list[str] = []

    async def emit(f: Any, win: Window) -> None:
        asked.append(f.summary)

    async def tick() -> None:
        await w.tick(direct, emit)

    screen.app = "chrome.exe"
    await tick()  # 不是写代码的程序：不截图
    screen.app = "code.exe"
    await tick()  # 第一次看到这个窗口：等它稳定
    await tick()  # 稳定了：整个窗口识别一次
    await tick()  # 没变：跳过
    assert (w.stats.other_app, w.stats.unsettled, w.stats.ocr, w.stats.unchanged) == (1, 1, 1, 1)
    assert ocr.crops == [(480, 640, 3)]

    # 底下冒出几行报错：先等输出停下，再只识别变了的那几行
    screen.draw(400, 440)
    ocr.next = TRACEBACK
    await tick()
    assert w.stats.unsettled == 2 and w.stats.ocr == 1
    await tick()
    assert w.stats.ocr == 2
    # 只识别变了的几行：往上多带 4 行格子（Traceback 的开头），往下多带 1 行
    assert ocr.crops[-1][0] == (439 // 16 + 2) * 16 - (400 // 16 - 4) * 16
    assert asked == ["TypeError: unsupported operand type(s) for *: 'NoneType' and 'int'"]

    # 同一个错误又出现（行号变了）：不再问
    screen.draw(300, 340, value=120)
    ocr.next = [line.replace("line 12", "line 13") for line in TRACEBACK]
    await tick()
    await tick()
    assert w.stats.errors == 2 and w.stats.duplicates == 1 and len(asked) == 1

    # 用户说「先别提醒」：连截图都不做
    w.pause(60)
    before = w.stats.ticks
    await tick()
    assert w.stats.ticks == before
    assert w.stats.summary()["ocr_avg_ms"] >= 0


def test_real_ocr_reads_a_rendered_terminal() -> None:
    """画一张假的终端截图，用真的 RapidOCR 识别，规则要认出 TypeError"""
    pytest.importorskip("rapidocr_onnxruntime")
    from PIL import Image, ImageDraw, ImageFont

    from app.screen.ocr import Ocr

    try:
        font = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 18)
    except OSError:
        pytest.skip("没有 Consolas 字体")
    img = Image.new("RGB", (1400, 300), (12, 12, 12))
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(TRACEBACK):
        draw.text((16, 16 + i * 30), line, fill=(204, 204, 204), font=font)
    lines = Ocr().lines(np.asarray(img)[:, :, ::-1].copy())

    f = detect(lines)
    assert f is not None and f.rule == "python"
    assert f.summary.startswith("TypeError: unsupported operand type")


# ---------- 接口 ----------


def test_screen_socket_reports_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    screen, ocr = FakeScreen(), FakeOcr()
    ocr.next = TRACEBACK
    monkeypatch.setattr(
        screen_route, "new_watcher",
        lambda: ScreenWatcher(lambda: ocr, capture=screen.capture, apps=frozenset({"code.exe"}), interval=0.01),
    )
    client = TestClient(app)
    with client.websocket_connect("/screen") as ws:
        ws.send_json({"type": "start"})
        assert ws.receive_json()["type"] == "started"
        msg = ws.receive_json()
        assert msg["type"] == "error_seen"
        assert msg["app"] == "VS Code" and msg["rule"] == "python"
        assert msg["summary"].startswith("TypeError")
        assert client.get("/screen/status").json()["watching"] is True
        ws.send_json({"type": "stop"})
        assert ws.receive_json()["type"] == "stopped"
    status = client.get("/screen/status").json()
    assert status["watching"] is False
    assert status["stats"]["asked"] == 1
    assert "code.exe" in status["apps"]


def test_screen_socket_rejects_other_websites() -> None:
    with pytest.raises(WebSocketDisconnect) as err:
        with TestClient(app).websocket_connect("/screen", headers={"Origin": "https://evil.example"}) as ws:
            ws.receive_json()
    assert err.value.code == 1008


def test_stop_when_desktop_disconnects(monkeypatch: pytest.MonkeyPatch) -> None:
    """桌面端断开（关掉、崩溃）：马上不看了"""
    screen, ocr = FakeScreen(), FakeOcr()
    monkeypatch.setattr(
        screen_route, "new_watcher",
        lambda: ScreenWatcher(lambda: ocr, capture=screen.capture, apps=frozenset({"code.exe"}), interval=0.01),
    )
    client = TestClient(app)
    with client.websocket_connect("/screen") as ws:
        ws.send_json({"type": "start"})
        assert ws.receive_json()["type"] == "started"
    for _ in range(50):
        if not client.get("/screen/status").json()["watching"]:
            break
        asyncio.run(asyncio.sleep(0.02))
    assert client.get("/screen/status").json()["watching"] is False
