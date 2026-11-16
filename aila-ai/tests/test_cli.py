"""终端版：渲染、斜杠命令、一次性模式、两种后端、交互模式（用模拟的键盘输入）"""

import asyncio
import io
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from app.cli.app import Terminal, headless, parse_args
from app.cli.backend import BackendError, EmbeddedBackend, HttpBackend
from app.cli.commands import changed_files, latest_todos, next_mode, parse, usage_lines
from app.cli.render import Renderer, split_markdown
from app.deps import get_runtime
from app.llm import Usage
from app.main import app
from app.runtime.runner import Runtime

from .test_runtime import SHELL, Scripted, call, project  # noqa: F401  （project 是 fixture）


def recording() -> Console:
    return Console(record=True, width=100, force_terminal=False, color_system=None, file=io.StringIO())


# ---------- 渲染 ----------


def test_split_markdown_keeps_code_blocks_whole() -> None:
    assert split_markdown("第一段\n\n第二段还没完") == ("第一段", "第二段还没完")
    text = "看这段：\n\n```py\na = 1\n\nb = 2"
    assert split_markdown(text) == ("看这段：", "```py\na = 1\n\nb = 2")  # 代码块没闭合，不从里面切
    done = text + "\n```\n\n后面"
    assert split_markdown(done)[1] == "后面"


def test_renderer_shows_tools_diffs_todos_and_stats() -> None:
    console = recording()
    r = Renderer(console)
    for e in [
        {"type": "user", "text": "加运费", "seq": 1},
        {"type": "text_delta", "text": "我先看看。\n\n"},
        {"type": "assistant", "text": "我先看看。\n\n", "tool_calls": [
            {"id": "a", "name": "edit_file", "arguments": json.dumps({"path": "cart.py"})},
            {"id": "b", "name": "run_command", "arguments": json.dumps({"command": "pytest"})},
            {"id": "c", "name": "todo_write", "arguments": "{}"}], "seq": 2},
        {"type": "usage", "prompt": 2000, "completion": 100, "cache_hit": 1500, "cache_miss": 500, "seq": 3},
        {"type": "tool", "id": "a", "name": "edit_file", "status": "ok", "content": "改好了 cart.py（+1 −1）",
         "diff": "--- a/cart.py\n+++ b/cart.py\n@@ -1 +1 @@\n-x\n+y\n", "file": "cart.py", "seq": 4},
        {"type": "tool", "id": "b", "name": "run_command", "status": "error",
         "content": "退出码 1 · 用时 0.5 秒\nok 1\nFAILED test_cart", "seq": 5},
        {"type": "tool", "id": "c", "name": "todo_write", "status": "ok", "content": "",
         "todos": [{"content": "改代码", "status": "completed"}, {"content": "跑测试", "status": "in_progress"}], "seq": 6},
        {"type": "turn_end", "reason": "done", "seq": 7},
    ]:
        r.event(e)
    out = console.export_text()
    assert "> 加运费" in out and "我先看看。" in out
    assert "● 改 cart.py" in out and "+y" in out and "-x" in out
    assert "● pytest" in out and "FAILED test_cart" in out
    assert "☑ 改代码" in out and "◐ 跑测试" in out
    assert "✓ 做完了" in out and "1 步" in out and "输入 2.0k（命中缓存 75%）" in out


def test_renderer_confirm_panel_and_replay() -> None:
    console = recording()
    r = Renderer(console)
    r.event({"type": "confirm", "id": "x", "kind": "command", "action": "跑测试", "detail": "npm test\n在 D:\\proj 里执行",
             "reason": "", "can_remember": True})
    r.replay([
        {"type": "user", "text": "以前的话"},
        {"type": "assistant", "text": "以前的回答", "tool_calls": [{"id": "r", "name": "read_file", "arguments": '{"path": "a.py"}'}]},
        {"type": "tool", "id": "r", "name": "read_file", "status": "ok", "content": "     1\tx = 1"},
        {"type": "turn_end", "reason": "interrupted"},
    ])
    out = console.export_text()
    assert "要跑测试，可以吗？" in out and "npm test" in out
    assert "以前的回答" in out and "● 读 a.py" in out and "读了 1 行" in out and "被你打断了" in out


# ---------- 斜杠命令 ----------


def test_commands() -> None:
    assert parse("/MODE auto") == ("/mode", "auto") and parse("/quit") == ("/exit", "")
    assert next_mode("ask") == "auto" and next_mode("ask", "只读") == "read_only" and next_mode("ask", "root") is None
    events = [
        {"type": "tool", "status": "ok", "file": "a.py"}, {"type": "tool", "status": "ok", "file": "a.py"},
        {"type": "tool", "status": "error", "file": "b.py"},
        {"type": "tool", "status": "ok", "todos": [{"content": "x", "status": "pending"}]},
    ]
    assert changed_files(events) == [("a.py", 2)] and latest_todos(events)[0]["content"] == "x"
    assert "75%" in usage_lines({"usage": {"prompt": 400, "cache_hit": 300, "completion": 5}})[0]
    args = parse_args(["-p", "做事", "--mode", "auto", "--local"])
    assert args.prompt == "做事" and args.mode == "auto" and args.local


# ---------- 一次性模式 ----------


@pytest.mark.anyio
async def test_headless_prints_final_answer(project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    model = Scripted([[call("read_file", path="README.md")], ["这是一个小商店。", Usage(10, 2, 0, 10)]])
    backend = EmbeddedBackend(Runtime(tmp_path / "data", lambda: model, shell=SHELL))
    info = await backend.create(str(project), "ask")
    code = await headless(backend, info["id"], "这是什么项目", err=recording())
    assert code == 0 and capsys.readouterr().out.strip() == "这是一个小商店。"


@pytest.mark.anyio
async def test_headless_cannot_ask_so_it_does_not_run(project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    model = Scripted([[call("write_file", path="new.txt", content="x")], ["不该到这里"]])
    backend = EmbeddedBackend(Runtime(tmp_path / "data", lambda: model, shell=SHELL))
    info = await backend.create(str(project), "ask")
    code = await headless(backend, info["id"], "建文件", json_lines=True)
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert code == 1 and not (project / "new.txt").exists()
    assert [e["result"] for e in lines if e["type"] == "confirm_end"] == ["rejected"]


# ---------- 走 AI 服务的后端 ----------


@pytest.mark.anyio
async def test_http_backend(project: Path, tmp_path: Path) -> None:
    model = Scripted([[call("list_files", pattern="*.md")], ["有 README.md。"]])
    rt = Runtime(tmp_path / "data", lambda: model, shell=SHELL)
    app.dependency_overrides[get_runtime] = lambda: rt
    backend = HttpBackend("http://testserver", transport=httpx.ASGITransport(app=app))
    try:
        info = await backend.create(str(project), "ask")
        events = [e async for e in backend.run(info["id"], "有哪些 md")]
        assert events[0]["type"] == "user" and events[-1]["type"] == "turn_end"
        await backend.set_mode(info["id"], "auto")
        assert (await backend.session(info["id"]))["mode"] == "auto"
        assert [s["id"] for s in await backend.sessions()] == [info["id"]]
        assert not await backend.answer("nope", True)
        with pytest.raises(BackendError, match="不是一个目录"):
            await backend.create(str(tmp_path / "nope"), "ask")
    finally:
        app.dependency_overrides.pop(get_runtime, None)
        await backend.close()


# ---------- 交互模式：模拟键盘 ----------


async def wait_for(cond: Any, timeout: float = 10) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not cond():
        assert loop.time() < end, "等太久了"
        await asyncio.sleep(0.02)


@pytest.mark.anyio
async def test_interactive_confirm_and_commands(project: Path, tmp_path: Path) -> None:
    model = Scripted([
        [call("read_file", path="README.md")],
        [call("edit_file", path="README.md", old_string="# 小商店", new_string="# 小商店（已改）")],
        ["改好了。"],
    ])
    backend = EmbeddedBackend(Runtime(tmp_path / "data", lambda: model, shell=SHELL))
    info = await backend.create(str(project), "ask")
    console = recording()
    with create_pipe_input() as inp:
        term = Terminal(backend, console, info, inp=inp, output=DummyOutput())
        task = asyncio.create_task(term.loop())
        inp.send_text("改一下标题\r")
        await wait_for(lambda: "要改 README.md，可以吗？" in console.export_text(clear=False))
        inp.send_text("2")  # 可以，以后都不用问
        await wait_for(lambda: term.renderer.reason == "done")
        inp.send_text("/changes\r")
        await wait_for(lambda: "  README.md" in console.export_text(clear=False))
        inp.send_text("/mode read_only\r")
        await wait_for(lambda: term.info["mode"] == "read_only")
        inp.send_text("/exit\r")
        assert await asyncio.wait_for(task, 10) == 0

    out = console.export_text()
    assert "可以（以后都不用问）" in out and "改好了。" in out and "权限模式：只读" in out
    assert (project / "README.md").read_text(encoding="utf-8").startswith("# 小商店（已改）")
    agent = backend.rt.agent(info["id"])
    assert agent.policy.edits_allowed and agent.policy.mode == "read_only"


@pytest.mark.anyio
async def test_escape_interrupts(project: Path, tmp_path: Path) -> None:
    model = Scripted([["我想", "想", "这", "个", "问", "题", "要", "很", "久"]], delay=0.3)
    backend = EmbeddedBackend(Runtime(tmp_path / "data", lambda: model, shell=SHELL))
    info = await backend.create(str(project), "ask")
    console = recording()
    with create_pipe_input() as inp:
        term = Terminal(backend, console, info, inp=inp, output=DummyOutput())
        task = asyncio.create_task(term.loop())
        inp.send_text("想个问题\r")
        await wait_for(lambda: len(model.requests) == 1)
        await asyncio.sleep(0.4)
        inp.send_text("\x1b")  # Esc
        await wait_for(lambda: term.renderer.reason == "interrupted")
        inp.send_text("/exit\r")
        assert await asyncio.wait_for(task, 10) == 0
    out = console.export_text()
    assert "正在打断" in out and "被你打断了" in out
    assert backend.rt.agent(info["id"]).session.events[-1]["reason"] == "interrupted"


@pytest.mark.anyio
async def test_branch_commands_in_the_terminal(tmp_path: Path) -> None:
    from .test_fix import make_repo

    repo = make_repo(tmp_path / "proj")
    backend = EmbeddedBackend(Runtime(tmp_path / "data", lambda: Scripted([["好"]]), shell=SHELL))
    info = await backend.create(str(repo), "ask", isolate=True)
    assert info["branch"].startswith("aila/task-")
    console = recording()
    with create_pipe_input() as inp:
        term = Terminal(backend, console, info, inp=inp, output=DummyOutput())
        term.renderer.header(info, backend.label)
        assert await term.command("/merge") == ""
        assert await term.command("/discard") == ""
    out = console.export_text()
    assert "/merge 跑测试并合并" in out and "没有合并：分支上还没有新的改动" in out and "放弃了分支 aila/task-" in out
    assert parse_args(["--branch"]).branch
