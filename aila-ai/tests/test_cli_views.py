"""终端里的执行轨迹（/trace）和上下文账本（/context）"""

import io
import json
from pathlib import Path

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from app.cli.app import Terminal
from app.cli.backend import EmbeddedBackend
from app.cli.render import Renderer
from app.cli.views import build_trace, ledger_view, trace_view
from app.llm import ToolCall, Usage
from app.runtime.runner import Runtime

from .test_runtime import SHELL, Scripted, project  # noqa: F401（project 是 fixture）

EVENTS = [
    {"type": "user", "seq": 1, "t": 100, "text": "修好运费"},
    {"type": "assistant", "seq": 2, "t": 102, "text": "", "timing": {"first": 0.8, "total": 2},
     "tool_calls": [{"id": "a", "name": "read_file", "arguments": '{"path": "cart.py"}'}]},
    {"type": "usage", "seq": 3, "t": 102, "prompt": 3000, "cache_hit": 0, "completion": 40},
    {"type": "tool", "seq": 4, "t": 102.1, "id": "a", "name": "read_file", "status": "ok", "seconds": 0.1},
    {"type": "assistant", "seq": 5, "t": 105, "text": "", "timing": {"first": 1.5, "total": 2.9},
     "tool_calls": [{"id": "b", "name": "edit_file", "arguments": '{"path": "cart.py"}'}]},
    {"type": "usage", "seq": 6, "t": 105, "prompt": 3400, "cache_hit": 3000, "completion": 80},
    {"type": "confirm", "seq": 7, "t": 105, "id": "c1", "action": "改 cart.py"},
    {"type": "confirm_end", "seq": 8, "t": 111, "id": "c1", "result": "approved"},
    {"type": "tool", "seq": 9, "t": 111.05, "id": "b", "name": "edit_file", "status": "ok", "seconds": 0.05},
    {"type": "assistant", "seq": 10, "t": 113, "text": "改好了。", "timing": {"first": 1, "total": 1.95}, "tool_calls": []},
    {"type": "usage", "seq": 11, "t": 113, "prompt": 3600, "cache_hit": 3400, "completion": 20},
    {"type": "turn_end", "seq": 12, "t": 113, "reason": "done"},
]


def recording() -> Console:
    return Console(record=True, width=120, force_terminal=False, color_system=None, file=io.StringIO())


def test_renderer_replays_background_jobs() -> None:
    console = recording()
    renderer = Renderer(console)
    renderer.replay([
        {"type": "assistant", "text": "", "tool_calls": [
            {"id": "a", "name": "run_command", "arguments": json.dumps({"command": "npm test", "background": False})},
            {"id": "b", "name": "run_command", "arguments": json.dumps({
                "command": "npm run dev", "description": "启动开发服务器", "background": True})},
            {"id": "c", "name": "job_output", "arguments": '{"id":"job-1"}'},
            {"id": "d", "name": "job_stop", "arguments": '{"id":"job-1"}'},
        ]},
        *[{"type": "tool", "id": key, "name": name, "status": "ok", "content": "job-1"}
          for key, name in (("a", "run_command"), ("b", "run_command"), ("c", "job_output"), ("d", "job_stop"))],
        {"type": "turn_end", "reason": "done"},
    ])
    out = console.export_text()
    assert "● npm test" in out and "● 后台：启动开发服务器" in out
    assert "● 看后台任务 job-1 的输出" in out and "● 停掉后台任务 job-1" in out


def test_trace_matches_the_window_version() -> None:
    [turn] = build_trace(EVENTS)
    assert turn.duration == 13 and turn.steps == 3
    assert [(r.kind, r.label) for r in turn.rows] == [
        ("model", "想下一步 → 读 cart.py"), ("tool", "读 cart.py"), ("model", "想下一步 → 改 cart.py"),
        ("wait", "等你确认：改 cart.py"), ("tool", "改 cart.py"), ("model", "回答"),
    ]
    assert (turn.rows[3].start, turn.rows[3].end) == (5, 11)
    console = recording()
    console.print(trace_view(turn))
    out = console.export_text()
    assert "轨迹 修好运费" in out and "命中缓存 64%" in out and "最慢：等你确认：改 cart.py 6.0s" in out
    assert "░" in out and "█" in out


def test_ledger_view() -> None:
    ledger = {
        "window": 128000, "compact_at": 0.8, "prompt": 12000, "cache_hit": 11000, "estimated": False, "messages": 9,
        "segments": [
            {"key": "system", "label": "系统提示词", "tokens": 800, "fixed": True},
            {"key": "tools", "label": "工具列表", "tokens": 1500, "fixed": True},
            {"key": "tool:read_file", "label": "工具结果：读文件", "tokens": 9700, "fixed": False},
        ],
        "biggest": [{"label": "读文件 src/big.py", "tokens": 6000}],
    }
    console = recording()
    console.print(ledger_view(ledger))
    out = console.export_text()
    assert "下一步输入 12.0k / 128.0k（9%）" in out and "上一步命中缓存 92%" in out
    assert "（固定，命中缓存）" in out and "读文件 src/big.py" in out and "|" in out


@pytest.mark.anyio
async def test_trace_and_context_commands(project: Path, tmp_path: Path) -> None:  # noqa: F811
    model = Scripted([[ToolCall("r", "read_file", json.dumps({"path": "README.md"})), Usage(900, 5, 0, 900)],
                      ["看完了。", Usage(1000, 5, 900, 100)]])
    backend = EmbeddedBackend(Runtime(tmp_path / "data", lambda: model, shell=SHELL))
    info = await backend.create(str(project), "ask")
    console = recording()
    with create_pipe_input() as inp:
        term = Terminal(backend, console, info, inp=inp, output=DummyOutput())
        await term.command("/trace")
        await term.turn("看 README")
        await term.command("/trace")
        await term.command("/context")
    out = console.export_text()
    assert "还没有做过事" in out
    assert "轨迹 看 README" in out and "读 README.md" in out
    assert "上下文账本" in out and "系统提示词" in out and "工具结果：读文件" in out


def test_trace_labels_background_jobs_and_helpers() -> None:
    """终端 /trace 的标题和任务页（fold.ts 的 actionOf）一样"""
    from app.cli.views import action

    assert action("run_command", {"command": "npm run dev", "background": True}) == "后台：npm run dev"
    assert action("job_output", {"id": "job-1"}) == "看后台任务 job-1 的输出"
    assert action("job_stop", {"id": "job-1"}) == "停掉后台任务 job-1"
    assert action("explore", {"task": "登录在哪处理"}) == "派助手去查「登录在哪处理」"
