"""「全部放开」权限、一直在网页上找时的提醒、读网页时带上 iframe 里的文字。"""

import asyncio
from pathlib import Path

import pytest

from app.browser.session import MAX_FRAMES, frames_text
from app.cli.commands import next_mode
from app.runtime.loop import BROWSER_NUDGE, BROWSER_WINDOW, BrowserStreak
from app.runtime.permissions import MODES, Policy, Rules
from app.runtime.runner import RuntimeError_, Runtime

from .test_runtime import SHELL, Scripted


class AllowNothing:
    def allowed(self, full_name: str) -> bool:
        return False

    def allow(self, full_name: str) -> None:
        raise AssertionError("全部放开不用记")


def policy(tmp_path: Path, mode: str) -> Policy:
    return Policy(tmp_path, Rules(tmp_path / "rules.json"), mode, mcp=AllowNothing())  # type: ignore[arg-type]


def test_full_mode_asks_for_nothing_ordinary(tmp_path: Path) -> None:
    full = policy(tmp_path, "full")
    for kind, command in [
        ("edit", ""), ("command", "npm run build"), ("memory", ""), ("delegate", ""),
        ("browser_local", "8123"), ("browser_interact", ""), ("browser_external", ""), ("browser_get", ""),
        ("mcp", "mcp__github__create_issue"),
    ]:
        assert full.decide(kind, command).verdict == "allow", kind


def test_full_mode_still_asks_for_dangerous_commands_and_secrets(tmp_path: Path) -> None:
    full = policy(tmp_path, "full")
    for command in ["rm -rf build", "git push --force", "npm publish", "reg add HKCU\\x", "cat .env"]:
        assert full.decide("command", command).verdict == "ask", command
    assert full.decide("secret", "").verdict == "ask"


def test_auto_mode_unchanged(tmp_path: Path) -> None:
    auto = policy(tmp_path, "auto")
    assert auto.decide("command", "npm run build").verdict == "allow"
    assert auto.decide("browser_local", "8123").verdict == "ask"
    assert auto.decide("browser_external", "").verdict == "ask"
    assert auto.decide("mcp", "mcp__github__create_issue").verdict == "ask"


def test_full_mode_is_for_projects_only(tmp_path: Path) -> None:
    assert "full" in MODES
    assert next_mode("auto") == "full" and next_mode("full") == "read_only"
    assert next_mode("ask", "全部放开") == "full"
    assert next_mode("ask", "full", computer=True) is None
    rt = Runtime(tmp_path / "data", lambda: Scripted([["好"]]), shell=SHELL)
    with pytest.raises(RuntimeError_):
        rt.create(str(tmp_path), "full", kind="computer")
    session = rt.create(str(tmp_path), "full")
    assert session.mode == "full"


def test_browser_streak_hints_once_per_window() -> None:
    streak = BrowserStreak()
    notes = [streak.note("browser_read") for _ in range(BROWSER_NUDGE)]
    assert all(not n for n in notes[:-1]) and "换个办法" in notes[-1]
    # 刚提醒过：接下来一个窗口里不再提醒
    assert not any(streak.note("browser_click") for _ in range(BROWSER_WINDOW - 1))
    assert "换个办法" in streak.note("browser_open")


def test_browser_streak_ignores_mixed_work() -> None:
    streak = BrowserStreak()
    for _ in range(20):
        assert not streak.note("read_file")
        assert not streak.note("browser_read")  # 一半一半：不算一直在网页上找


class FakeFrame:
    def __init__(self, url: str, text: str | Exception, detached: bool = False) -> None:
        self.url, self.text, self.detached = url, text, detached

    def is_detached(self) -> bool:
        return self.detached

    async def evaluate(self, js: str, n: int) -> str:
        if isinstance(self.text, Exception):
            raise self.text
        return self.text[:n]


class FakePage:
    def __init__(self, frames: list[FakeFrame]) -> None:
        self.main_frame = FakeFrame("http://127.0.0.1:8123/", "主页面")
        self.frames = [self.main_frame, *frames]


def test_frames_text_reads_iframes() -> None:
    page = FakePage([
        FakeFrame("http://127.0.0.1:8123/resume/index.html?embed=1", "陈往\n前端工程师"),
        FakeFrame("http://127.0.0.1:8123/broken", RuntimeError("跨域")),
        FakeFrame("http://127.0.0.1:8123/gone", "不该出现", detached=True),
    ])
    text = asyncio.run(frames_text(page))
    assert "resume/index.html?embed=1" in text and "前端工程师" in text
    assert "broken" not in text and "不该出现" not in text and "主页面" not in text
    assert "browser_open" in text  # 告诉她要操作就直接打开


def test_frames_text_empty_without_frames_and_caps_count() -> None:
    assert asyncio.run(frames_text(FakePage([]))) == ""
    assert asyncio.run(frames_text(object())) == ""
    many = FakePage([FakeFrame(f"http://127.0.0.1:8123/{i}", f"第 {i} 个") for i in range(MAX_FRAMES + 2)])
    text = asyncio.run(frames_text(many))
    assert f"[iframe {MAX_FRAMES}]" in text and f"[iframe {MAX_FRAMES + 1}]" not in text
    assert "还有 2 个 iframe" in text
