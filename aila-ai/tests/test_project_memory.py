"""项目记忆：存取、放进会话开头、remember 工具按权限模式、接口和终端"""

import io
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from app.cli.app import Terminal
from app.cli.backend import BackendError, EmbeddedBackend, HttpBackend
from app.deps import get_runtime
from app.main import app
from app.runtime.memory import CONTEXT_CHARS, NOTE_CHARS, NOTE_LIMIT, MemoryError_, ProjectMemory, project_key
from app.runtime.runner import Runtime

from .test_runtime import SHELL, Scripted, call


def project(tmp_path: Path, name: str = "shop") -> Path:
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    return root


# ---------- 存取 ----------


def test_add_dedupe_remove_and_path_spelling(tmp_path: Path) -> None:
    mem = ProjectMemory(tmp_path / "m.json")
    root = project(tmp_path)
    note, added = mem.add(root, "  测试用 pytest -q，\n别用 unittest  ", "agent", "s1")
    assert added and note.text == "测试用 pytest -q， 别用 unittest" and note.source == "agent" and note.session == "s1"
    # 同一个项目换个写法（大小写、斜杠、末尾斜杠）还是同一份
    other_spelling = str(root).upper().replace("\\", "/") + "/"
    again, added = mem.add(other_spelling, "测试用 pytest -q， 别用 unittest")
    assert not added and again.id == note.id
    assert [n.text for n in mem.notes(other_spelling)] == [note.text]
    assert mem.notes(project(tmp_path, "other")) == []
    assert not mem.remove(root, "nope")
    assert mem.remove(root, note.id) and mem.notes(root) == []
    assert json.loads((tmp_path / "m.json").read_text(encoding="utf-8")) == {}  # 删空了不留空壳


def test_rejects_empty_and_too_long_and_keeps_latest(tmp_path: Path) -> None:
    mem = ProjectMemory(tmp_path / "m.json")
    root = project(tmp_path)
    with pytest.raises(MemoryError_, match="空的"):
        mem.add(root, "  \n ")
    with pytest.raises(MemoryError_, match=str(NOTE_CHARS)):
        mem.add(root, "长" * (NOTE_CHARS + 1))
    for i in range(NOTE_LIMIT + 5):
        mem.add(root, f"第 {i} 条")
    texts = [n.text for n in mem.notes(root)]
    assert len(texts) == NOTE_LIMIT and texts[0] == "第 5 条" and texts[-1] == f"第 {NOTE_LIMIT + 4} 条"


def test_broken_file_reads_as_empty(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    mem = ProjectMemory(path)
    root = project(tmp_path)
    for content in ("{", "[]", json.dumps({project_key(root): {"notes": ["x", {"text": 1}, {"id": "a", "text": "好的"}]}})):
        path.write_text(content, encoding="utf-8")
        assert [n.text for n in mem.notes(root)] in ([], ["好的"])


def test_context_block_has_newest_within_limit(tmp_path: Path) -> None:
    mem = ProjectMemory(tmp_path / "m.json")
    root = project(tmp_path)
    assert mem.context(root) == ""
    for i in range(NOTE_LIMIT):
        mem.add(root, f"{i:02d} " + "字" * 200)
    block = mem.context(root)
    assert block.startswith("\n<项目记忆") and block.endswith("</项目记忆>")
    assert len(block) < CONTEXT_CHARS + 200
    assert f"{NOTE_LIMIT - 1:02d} " in block and "00 " not in block  # 放不下时丢最早的


# ---------- 会话开头、remember 工具 ----------


@pytest.mark.anyio
async def test_new_sessions_start_with_memory_and_old_ones_keep_their_context(tmp_path: Path) -> None:
    root = project(tmp_path)
    rt = Runtime(tmp_path / "data", lambda: Scripted([["好"]]), shell=SHELL)
    first = rt.create(str(root))
    before = first.events[0]["context"]
    assert "<项目记忆" not in before
    rt.memory.add(root, "改完跑 npm test")
    assert first.events[0]["context"] == before  # 已经开着的会话不改开头（前缀缓存）
    second = rt.create(str(root))
    assert "<项目记忆" in second.events[0]["context"] and "- 改完跑 npm test" in second.events[0]["context"]
    other = rt.create(str(project(tmp_path, "other")))
    assert "<项目记忆" not in other.events[0]["context"]
    await rt.shutdown()


@pytest.mark.anyio
async def test_remember_tool_follows_permission_mode(tmp_path: Path) -> None:
    root = project(tmp_path)
    script = [[call("remember", text="构建用 npm run build:prod")], ["记好了"]]

    rt = Runtime(tmp_path / "data", lambda: Scripted(script), shell=SHELL)
    sid = rt.create(str(root), "auto").id
    events = [e async for e in rt.run(sid, "记一下构建命令")]
    tool = next(e for e in events if e["type"] == "tool")
    assert tool["status"] == "ok" and tool["memory"]["text"] == "构建用 npm run build:prod"
    assert [(n.text, n.source, n.session) for n in rt.memory.notes(root)] == [("构建用 npm run build:prod", "agent", sid)]

    sid = rt.create(str(root), "read_only").id
    events = [e async for e in rt.run(sid, "记一下")]
    assert next(e for e in events if e["type"] == "tool")["status"] == "denied"

    rt.memory.remove(root, rt.memory.notes(root)[0].id)
    sid = rt.create(str(root), "ask").id
    seen = []
    async for e in rt.run(sid, "记一下"):
        seen.append(e)
        if e["type"] == "confirm":
            # 问你时给你看的是要存的原话，由代码拼出来
            assert e["kind"] == "memory" and "构建用 npm run build:prod" in e["detail"] and not e["can_remember"]
            rt.confirms.answer(e["id"], False)
    assert next(e for e in seen if e["type"] == "tool")["status"] == "rejected"
    assert rt.memory.notes(root) == []
    await rt.shutdown()


def test_system_prompt_and_tool_list_mention_remember(tmp_path: Path) -> None:
    from app.runtime.prompt import SYSTEM_PROMPT

    rt = Runtime(tmp_path / "data", lambda: Scripted([["好"]]), shell=SHELL)
    sid = rt.create(str(project(tmp_path))).id
    names = [t["function"]["name"] for t in rt.agent(sid).tools.specs]
    assert names[-1] == "remember" and "remember" in SYSTEM_PROMPT


# ---------- 接口 ----------


@pytest.fixture
def client(tmp_path: Path):
    rt = Runtime(tmp_path / "data", lambda: Scripted([["好"]]), shell=SHELL)
    app.dependency_overrides[get_runtime] = lambda: rt
    c = TestClient(app)
    try:
        yield c, rt
    finally:
        c.close()
        app.dependency_overrides.pop(get_runtime, None)


def test_memory_api(client, tmp_path: Path) -> None:
    c, rt = client
    root = str(project(tmp_path))
    assert c.get("/agent/memory", params={"cwd": root}).json() == []
    res = c.post("/agent/memory", json={"cwd": root, "text": "别改 legacy/ 目录"})
    assert res.status_code == 201 and res.json()["added"] and res.json()["source"] == "user"
    assert c.post("/agent/memory", json={"cwd": root, "text": "别改 legacy/ 目录"}).json()["added"] is False
    notes = c.get("/agent/memory", params={"cwd": root}).json()
    assert [n["text"] for n in notes] == ["别改 legacy/ 目录"]
    assert c.post("/agent/memory", json={"cwd": root, "text": "长" * (NOTE_CHARS + 1)}).status_code == 422
    assert c.request("DELETE", "/agent/memory", json={"cwd": root, "id": "nope"}).status_code == 404
    assert c.request("DELETE", "/agent/memory", json={"cwd": root, "id": notes[0]["id"]}).json() == {"ok": True}
    assert rt.memory.notes(root) == []
    assert c.get("/agent/memory", params={"cwd": root}, headers={"origin": "https://example.com"}).status_code == 403


# ---------- 终端 ----------


@pytest.mark.anyio
@pytest.mark.parametrize("backend_kind", ["embedded", "http"])
async def test_terminal_memory_command(tmp_path: Path, backend_kind: str) -> None:
    root = project(tmp_path)
    rt = Runtime(tmp_path / "data", lambda: Scripted([["好"]]), shell=SHELL)
    app.dependency_overrides[get_runtime] = lambda: rt
    backend = EmbeddedBackend(rt) if backend_kind == "embedded" else HttpBackend("http://testserver", transport=httpx.ASGITransport(app=app))
    console = Console(file=io.StringIO(), record=True, width=160, force_terminal=False, color_system=None)
    try:
        info = await backend.create(str(root), "ask")
        with create_pipe_input() as inp:
            term = Terminal(backend, console, info, inp=inp, output=DummyOutput())
            await term.command("/memory")
            assert "还没有记忆" in console.export_text(clear=True)
            await term.command("/memory add 改完跑 [bold]npm test[/bold]")
            assert "记下了" in console.export_text(clear=True)
            await term.command("/memory add 改完跑 [bold]npm test[/bold]")
            assert "已经记过了" in console.export_text(clear=True)
            await term.command("/memory")
            out = console.export_text(clear=True)
            assert "1. 改完跑 [bold]npm test[/bold]" in out and "你加的" in out  # 原文显示，不当成格式
            for bad in ("/memory forget 0", "/memory forget 9", "/memory forget x", "/memory oops"):
                await term.command(bad)
            assert len(rt.memory.notes(root)) == 1
            await term.command("/memory forget 1")
            assert rt.memory.notes(root) == []
        with pytest.raises(BackendError, match="没有这条"):
            await backend.forget_memory(str(root), "nope")
    finally:
        app.dependency_overrides.pop(get_runtime, None)
        await backend.close()
        await rt.shutdown()
