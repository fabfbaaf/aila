"""自定义命令：命令文件怎么读、项目盖过自己的、「/名字 参数」怎么展开、几个入口（运行时、HTTP、终端）都认"""

import asyncio
from pathlib import Path

import httpx
import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from app.cli.app import Terminal
from app.cli.backend import EmbeddedBackend, HttpBackend
from app.deps import get_runtime
from app.main import app
from app.runtime.commands import load_commands, match, parse_file
from app.runtime.runner import Runtime

from .test_cli import recording, wait_for
from .test_runtime import SHELL, Scripted, project  # noqa: F401  （project 是 fixture）

RELEASE = """---
description: 发版：跑测试、改版本号、写更新说明
---
按这个顺序发版本 $ARGUMENTS：
1. 跑全部测试
2. 改版本号
"""


def write(folder: Path, name: str, text: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_parse_command_file(tmp_path: Path) -> None:
    cmd = parse_file(write(tmp_path, "发布", RELEASE), "project")
    assert cmd and cmd.name == "发布" and cmd.description == "发版：跑测试、改版本号、写更新说明"
    assert cmd.expand("v1.2").startswith("按这个顺序发版本 v1.2：")
    assert "description" not in cmd.body
    # 没有说明：用第一行；没有 $ARGUMENTS：参数接在最后
    plain = parse_file(write(tmp_path, "周报", "# 写周报\n把这周的提交整理成周报。\n"), "user")
    assert plain and plain.description == "写周报"
    assert plain.expand("") == "# 写周报\n把这周的提交整理成周报。"
    assert plain.expand("只写 aila-ai").endswith("补充说明：只写 aila-ai")
    # 内置命令的名字、怪名字、空文件都不算
    assert parse_file(write(tmp_path, "review", "审一下"), "project") is None
    assert parse_file(write(tmp_path, "a b", "x"), "project") is None
    assert parse_file(write(tmp_path, "空的", "\n\n"), "project") is None


def test_project_overrides_user_and_match(tmp_path: Path) -> None:
    proj, mine = tmp_path / "proj", tmp_path / "mine"
    write(mine, "发布", "我的发布流程")
    write(mine, "周报", "写周报")
    write(proj / ".aila" / "commands", "发布", RELEASE)
    found = load_commands(proj, mine)
    assert found["发布"].source == "project" and found["周报"].source == "user"
    assert load_commands(None, mine)["发布"].source == "user"  # 电脑任务只用自己的
    cmd, args = match("/发布 v1.2 先别推送", found)  # type: ignore[misc]
    assert cmd.name == "发布" and args == "v1.2 先别推送"
    assert match("/发布\n第二行", found)[1] == "第二行"  # type: ignore[index]
    assert match("/没有这个", found) is None and match("//发布", found) is None and match("发布", found) is None


@pytest.mark.anyio
async def test_runtime_expands_and_records_the_command(project: Path, tmp_path: Path) -> None:
    write(project / ".aila" / "commands", "发布", RELEASE)
    model = Scripted([["好，按步骤来。"]])
    rt = Runtime(tmp_path / "data", lambda: model, shell=SHELL)
    session = rt.create(str(project), "ask")
    events = [e async for e in rt.run(session.id, "/发布 v1.2")]
    user = events[0]
    assert user["type"] == "user" and user["command"] == "发布" and user["title"] == "/发布 v1.2"
    assert user["text"].startswith("按这个顺序发版本 v1.2：")
    assert "按这个顺序发版本 v1.2" in model.requests[0][0][-1]["content"]  # 交给模型的是展开后的全文
    assert session.title == "/发布 v1.2"
    # 不是命令的斜杠开头的话原样发
    model2 = Scripted([["嗯"]])
    rt.model_factory = lambda: model2
    events = [e async for e in rt.run(session.id, "/不认识 的话")]
    assert events[0]["text"] == "/不认识 的话" and "command" not in events[0]
    await rt.shutdown()


@pytest.mark.anyio
async def test_commands_over_http_and_in_the_terminal(project: Path, tmp_path: Path) -> None:
    write(project / ".aila" / "commands", "发布", RELEASE)
    model = Scripted([["发好了。"]])
    rt = Runtime(tmp_path / "data", lambda: model, shell=SHELL)
    write(rt.data_dir / "commands", "周报", "写周报")
    app.dependency_overrides[get_runtime] = lambda: rt
    http = HttpBackend("http://testserver", transport=httpx.ASGITransport(app=app))
    try:
        listed = await http.commands(str(project))
        assert [c["name"] for c in listed["commands"]] == ["发布", "周报"]
        assert listed["project_dir"].endswith("commands") and listed["user_dir"].endswith("commands")
        computer = await http.commands(None, "computer")
        assert [c["name"] for c in computer["commands"]] == ["周报"] and computer["project_dir"] is None
    finally:
        app.dependency_overrides.pop(get_runtime, None)
        await http.close()

    backend = EmbeddedBackend(rt)
    info = await backend.create(str(project), "ask")
    console = recording()
    with create_pipe_input() as inp:
        term = Terminal(backend, console, info, inp=inp, output=DummyOutput())
        task = asyncio.create_task(term.loop())
        inp.send_text("/commands\r")
        await wait_for(lambda: "发版：跑测试" in console.export_text(clear=False))
        inp.send_text("/发布 v2\r")
        await wait_for(lambda: term.renderer.reason == "done")
        inp.send_text("/exit\r")
        assert await asyncio.wait_for(task, 10) == 0
    out = console.export_text()
    assert "按自定义命令 /发布 做" in out and "发好了。" in out
    assert rt.agent(info["id"]).session.events[1]["command"] == "发布"
