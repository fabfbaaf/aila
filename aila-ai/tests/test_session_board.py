"""看板用的会话摘要：她在干嘛（activity）、上一轮怎么结束的（outcome）、改了几个文件（edits）、等你确认的那件事（pending）"""

import asyncio
from pathlib import Path

import pytest

from app.runtime.runner import Runtime

from .test_runtime import SHELL, Scripted, call, project  # noqa: F401  （project 是 fixture）


@pytest.mark.anyio
async def test_summary_tells_what_she_is_doing(project: Path, tmp_path: Path) -> None:
    model = Scripted([
        [call("read_file", path="README.md")],
        [call("edit_file", path="README.md", old_string="# 小商店", new_string="# 小商店（改）")],
        ["改好了。"],
    ])
    rt = Runtime(tmp_path / "data", lambda: model, shell=SHELL)
    session = rt.create(str(project), "ask")
    assert session.summary()["activity"] is None and session.summary()["outcome"] is None

    stream = rt.run(session.id, "改标题")
    pending = None
    async for e in stream:
        if e["type"] == "confirm":
            summary = session.summary()
            pending = summary["pending"]
            assert summary["waiting"] == "confirm"
            assert summary["activity"] == {"tool": "edit_file", "target": "README.md"}
            rt.confirms.answer(e["id"], True)
    assert pending and pending["tool"] == "edit_file" and "README.md" in pending["action"]

    done = session.summary()
    assert done["pending"] is None and done["outcome"] == "done" and done["edits"] == 1
    await rt.shutdown()


@pytest.mark.anyio
async def test_interrupted_turn_outcome(project: Path, tmp_path: Path) -> None:
    rt = Runtime(tmp_path / "data", lambda: Scripted([["慢慢想"]], delay=5), shell=SHELL)
    session = rt.create(str(project), "auto")
    rt.start(session.id, "想一想")
    await asyncio.sleep(0.2)
    rt.interrupt(session.id)
    for _ in range(100):
        if not rt.running(session.id):
            break
        await asyncio.sleep(0.05)
    assert session.summary()["outcome"] == "interrupted"
    await rt.shutdown()
