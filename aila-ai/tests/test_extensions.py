"""扩展包：说明书检查、导入（文件夹、zip、不安全的包）、装卸和开关、命令和本事接进编程 Agent、
工具服务接进 MCP（用真的子进程）、HTTP 接口"""

import json
import shutil
import sys
import zipfile
from pathlib import Path

import httpx
import pytest

from app.extensions import (
    ExtensionError,
    ExtensionStore,
    parse_manifest,
    read_skill_file,
    skills_context,
    version_ok,
)
from app.llm import ToolCall
from app.runtime.mcp import McpManager
from app.runtime.runner import Runtime

from .test_runtime import SHELL, Scripted, project  # noqa: F401  （project 是 fixture）

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "extensions"


def make_ext(root: Path, ext_id: str = "demo", **manifest) -> Path:
    folder = root / ext_id
    (folder / "commands").mkdir(parents=True)
    (folder / "skills" / "写周报").mkdir(parents=True)
    (folder / "commands" / "周报.md").write_text("---\ndescription: 写这周的周报\n---\n按提交写周报 $ARGUMENTS\n", encoding="utf-8")
    (folder / "skills" / "写周报" / "SKILL.md").write_text(
        "---\ndescription: 周报的格式和语气\n---\n# 周报\n三段：做了什么、卡在哪、下周做什么。\n", encoding="utf-8")
    (folder / "skills" / "写周报" / "例子.md").write_text("本周：修了登录。", encoding="utf-8")
    data = {"id": ext_id, "name": "演示", "version": "1.0.0", "aila": ">=0.1", **manifest}
    (folder / "aila-extension.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return folder


def fake_server_ext(root: Path) -> Path:
    """带工具服务的扩展：用测试里的假 MCP 服务（真的子进程）"""
    folder = make_ext(root, "fakeext", mcpServers={"srv": {
        "command": sys.executable, "args": [str(Path(__file__).with_name("fake_mcp_server.py"))],
        "env": {"FAKE_TOKEN": "${setting:token}"}}},
        settings=[{"key": "token", "label": "令牌", "secret": True, "required": True}])
    return folder


# ---------- 说明书 ----------


def test_manifest_checks() -> None:
    ok = parse_manifest({"id": "a-b", "version": "1.2", "name": "甲"})
    assert (ok.id, ok.name, ok.servers, ok.settings) == ("a-b", "甲", {}, [])
    for bad, why in [
        ({"id": "A_B", "version": "1"}, "扩展 id"),
        ({"id": "ab"}, "缺 version"),
        ({"id": "ab", "version": "v1"}, "版本号"),
        ({"id": "ab", "version": "1", "mcpServers": {"toolong12": {"command": "x"}}}, "工具服务名"),
        ({"id": "ab", "version": "1", "mcpServers": {"s": {"url": "http://x"}}}, "command"),
        ({"id": "ab", "version": "1", "mcpServers": {"s": {"command": "x", "env": {"K": "${setting:nope}"}}}}, "没在 settings"),
        ({"id": "ab", "version": "1", "settings": [{"key": "1x"}]}, "key"),
        ({"id": "ab", "version": "1", "requires": ["rm -rf"]}, "requires"),
    ]:
        with pytest.raises(ExtensionError, match=why):
            parse_manifest(bad)


def test_version_requirement() -> None:
    assert version_ok("") and version_ok(">=0.1") and version_ok(">=0.1.0", "0.1.0")
    assert not version_ok(">=0.2", "0.1.9") and version_ok(">=1", "1.0.0")
    with pytest.raises(ExtensionError, match=">="):
        version_ok("^1.0")


# ---------- 导入 ----------


def test_stage_folder_shows_plan_then_install(tmp_path: Path) -> None:
    src = fake_server_ext(tmp_path / "src")
    store = ExtensionStore(tmp_path / "data" / "extensions")
    token, plan = store.stage(src)
    # 清单由代码算出来：命令、本事、要运行的程序、要填的设置、提醒
    assert plan["commands"] == ["周报"] and plan["skills"] == [{"name": "写周报", "description": "周报的格式和语气"}]
    assert plan["servers"][0]["name"] == "srv" and sys.executable in plan["servers"][0]["command"]
    assert plan["servers"][0]["env"] == ["FAKE_TOKEN"] and plan["settings"][0]["secret"]
    assert any("运行下面的程序" in w for w in plan["warnings"]) and any("做事说明" in w for w in plan["warnings"])
    assert store.installed() == []  # 只是暂存，还没装

    ext = store.install(token)
    assert ext.id == "fakeext" and ext.enabled and ext.server_disabled == {"srv": True}  # 工具服务默认停着
    assert not (store.root / ".staging" / token).exists()
    with pytest.raises(ExtensionError, match="重新导入"):
        store.install(token)

    # 必填的设置没填不能打开服务；填了以后服务配置里换成真的值，界面上只说填没填
    with pytest.raises(ExtensionError, match="令牌"):
        store.set_server_disabled("fakeext", "srv", False)
    store.set_settings("fakeext", {"token": "s3cret"})
    store.set_server_disabled("fakeext", "srv", False)
    servers = store.mcp_servers()
    assert servers["fakeext-srv"]["env"] == {"FAKE_TOKEN": "s3cret"} and not servers["fakeext-srv"]["disabled"]
    assert servers["fakeext-srv"]["cwd"] == str(store.root / "fakeext")
    shown = store.describe(store.get("fakeext"))
    assert shown["settings"][0]["set"] and shown["settings"][0]["value"] == ""
    assert "s3cret" not in json.dumps(shown)
    with pytest.raises(ExtensionError, match="没有 nope"):
        store.set_settings("fakeext", {"nope": "x"})

    # 升级：开关和设置保留
    (src / "aila-extension.json").write_text(
        (src / "aila-extension.json").read_text(encoding="utf-8").replace('"1.0.0"', '"1.1.0"'), encoding="utf-8")
    token2, plan2 = store.stage(src)
    assert plan2["upgrade"] and any("换成 1.1.0" in w for w in plan2["warnings"])
    ext = store.install(token2)
    assert ext.manifest.version == "1.1.0" and ext.server_disabled == {"srv": False} and ext.settings == {"token": "s3cret"}

    # 停用：命令、本事、服务都不给；卸载：文件夹和状态都删掉
    store.set_enabled("fakeext", False)
    assert store.mcp_servers() == {} and store.skills() == [] and store.command_dirs() == []
    store.uninstall("fakeext")
    assert store.installed() == [] and not (store.root / "fakeext").exists()
    assert "fakeext" not in json.loads((store.root / "state.json").read_text(encoding="utf-8"))


def test_zip_with_wrapping_folder_and_unsafe_zips(tmp_path: Path) -> None:
    src = make_ext(tmp_path / "src")
    good = tmp_path / "good.zip"
    with zipfile.ZipFile(good, "w") as z:  # 右键压缩：外面多一层文件夹
        for f in src.rglob("*"):
            if f.is_file():
                z.write(f, "demo-1.0/" + f.relative_to(src).as_posix())
    store = ExtensionStore(tmp_path / "exts")
    token, plan = store.stage(good)
    assert plan["id"] == "demo" and plan["commands"] == ["周报"]
    store.discard(token)
    assert not (store.root / ".staging" / token).exists()

    for name, member in [("evil.zip", "../evil.txt"), ("abs.zip", "C:/evil.txt"), ("none.zip", "readme.txt")]:
        bad = tmp_path / name
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr(member, "x")
        with pytest.raises(ExtensionError):
            store.stage(bad)
    assert not (tmp_path / "evil.txt").exists()
    assert list((store.root / ".staging").iterdir()) == []  # 失败的暂存都清掉了

    not_zip = tmp_path / "x.txt"
    not_zip.write_text("hi", encoding="utf-8")
    with pytest.raises(ExtensionError, match="文件夹或者 .zip"):
        store.stage(not_zip)


def test_skill_files_stay_inside_their_folder(tmp_path: Path) -> None:
    src = make_ext(tmp_path / "src")
    store = ExtensionStore(tmp_path / "exts")
    store.install(store.stage(src)[0])
    skill = store.skills()[0]
    assert "三段" in read_skill_file(skill) and read_skill_file(skill, "例子.md") == "本周：修了登录。"
    for bad in ["../../aila-extension.json", "/etc/passwd", "C:\\Windows\\win.ini", "没有.md"]:
        with pytest.raises(ExtensionError):
            read_skill_file(skill, bad)
    assert "read_skill" in skills_context([skill]) and "来自扩展「演示」" in skills_context([skill])
    assert skills_context([]) == ""


def test_bare_skill_folders_are_wrapped(tmp_path: Path) -> None:
    """没有说明书、只有 SKILL.md 的文件夹也能导入：自动包成一个只带本事的扩展"""
    store = ExtensionStore(tmp_path / "exts")

    # 1. SKILL.md 就在最外面；名字取开头的 name，别的文件跟着走
    one = tmp_path / "Code Review"
    one.mkdir()
    (one / "SKILL.md").write_text("---\nname: review\ndescription: 怎么审代码\n---\n先看测试。\n", encoding="utf-8")
    (one / "checklist.md").write_text("- 有没有测试", encoding="utf-8")
    token, plan = store.stage(one)
    assert plan["id"] == "code-review" and plan["skills"] == [{"name": "review", "description": "怎么审代码"}]
    ext = store.install(token)
    skill = store.skills()[0]
    assert ext.manifest.version == "1.0.0" and read_skill_file(skill, "checklist.md") == "- 有没有测试"
    assert not (one / "aila-extension.json").exists()  # 原来的文件夹不动

    # 2. 一个文件夹里好几项，zip 外面还多包了一层；中文名的 id 用短号，再导入还是同一个
    many = tmp_path / "技能合集.zip"
    with zipfile.ZipFile(many, "w") as z:
        z.writestr("技能合集/写周报/SKILL.md", "# 周报的写法\n三段。")
        z.writestr("技能合集/翻译/SKILL.md", "---\ndescription: 中译英\n---\n直译。")
        z.writestr("技能合集/README.md", "说明")
    token, plan = store.stage(many)
    assert plan["id"].startswith("skill-") and sorted(s["name"] for s in plan["skills"]) == ["写周报", "翻译"]
    assert store.stage(many)[1]["id"] == plan["id"]

    # 3. 什么都不是：照常报没有说明书
    empty = tmp_path / "空的"
    empty.mkdir()
    (empty / "readme.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ExtensionError, match="没有 aila-extension.json"):
        store.stage(empty)


def test_examples_in_repo_are_valid(tmp_path: Path) -> None:
    store = ExtensionStore(tmp_path / "exts")
    git_plan = store.stage(EXAMPLES / "git-helper")[1]
    assert set(git_plan["commands"]) == {"changelog", "explain-diff"} and git_plan["servers"] == []
    assert [s["name"] for s in git_plan["skills"]] == ["conventional-commits"]
    dice_plan = store.stage(EXAMPLES / "dice")[1]
    assert dice_plan["servers"][0]["readOnly"] and "<扩展文件夹>/server.py" in dice_plan["servers"][0]["command"]


@pytest.mark.anyio
async def test_builtin_research_and_video_reach_new_tasks(tmp_path: Path, project: Path) -> None:  # noqa: F811
    model = Scripted([
        [ToolCall("paper", "read_skill", '{"name":"paper-lookup"}')],
        [ToolCall("video", "read_skill", '{"name":"hyperframes-video"}')],
        [ToolCall("example", "read_skill", '{"name":"hyperframes-video","file":"examples/title-card.html"}')],
        ["读完了"],
    ])
    rt = Runtime(tmp_path / "data", lambda: model, shell=SHELL)
    try:
        assert rt.extensions.skills() == []
        commands = rt.commands(str(project))
        assert commands["paper-lookup"].source == "builtin"
        assert commands["make-video"].source == "builtin"
        assert "brief.md" in commands["make-video"].expand("brief.md")
        session = rt.create(str(project), "ask")
        assert "paper-lookup" in session.context and "hyperframes-video" in session.context
        desktop_task = rt.create(kind="computer", mode="ask")
        assert "render_video" in rt.agent(desktop_task.id).tools.tools
        assert "web_fetch" in rt.agent(desktop_task.id).tools.tools
        events = [event async for event in rt.run(session.id, "先查资料，再做视频")]
        results = [event["content"] for event in events if event["type"] == "tool"]
        assert len(results) == 3
        assert "web_fetch" in results[0]
        assert "艾拉内置" in results[1] and "render_video" in results[1]
        assert 'data-composition-id="main"' in results[2]
    finally:
        await rt.shutdown()


# ---------- 接进编程 Agent ----------


@pytest.mark.anyio
async def test_commands_and_skills_reach_the_agent(tmp_path: Path, project: Path) -> None:  # noqa: F811
    def skill_call(**args: str) -> ToolCall:  # call() 自己有个 name 参数，这里直接拼
        return ToolCall(f"s{len(args)}", "read_skill", json.dumps(args, ensure_ascii=False))

    model = Scripted([[skill_call(name="写周报")], [skill_call(name="写周报", file="例子.md")], ["写好了"]])
    rt = Runtime(tmp_path / "data", lambda: model, shell=SHELL)
    rt.extensions.install(rt.extensions.stage(make_ext(tmp_path / "src"))[0])

    # 命令：扩展的能用；你自己的同名命令盖过扩展的
    cmds = rt.commands(str(project))
    assert cmds["周报"].source == "extension:demo"
    (tmp_path / "data" / "commands").mkdir(parents=True)
    (tmp_path / "data" / "commands" / "周报.md").write_text("我自己的周报写法", encoding="utf-8")
    assert rt.commands(str(project))["周报"].source == "user"

    # 本事：开会话时列进环境说明，给 read_skill 工具；读全文、读附带文件
    session = rt.create(str(project), "ask")
    assert "<可用的本事>" in session.context and "写周报" in session.context
    events = [e async for e in rt.run(session.id, "写这周的周报")]
    results = [e for e in events if e["type"] == "tool"]
    assert "三段" in results[0]["content"] and "本周：修了登录。" in results[1]["content"]
    assert any(t["function"]["name"] == "read_skill" for t in model.requests[0][1])

    # 扩展长处按会话记，停用后，新任务仍可使用内置本事。
    rt.extensions.set_enabled("demo", False)
    plain = rt.create(str(project), "ask")
    assert "写周报" not in plain.context
    assert "paper-lookup" in plain.events[0]["skills"]
    await rt.shutdown()


@pytest.mark.anyio
async def test_extension_servers_join_mcp(tmp_path: Path) -> None:
    store = ExtensionStore(tmp_path / "exts")
    store.install(store.stage(fake_server_ext(tmp_path / "src"))[0])
    mcp_json = tmp_path / "mcp.json"
    mcp_json.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    changes: list[str] = []

    async def changed() -> None:
        changes.append("x")

    m = McpManager(mcp_json, extensions=store)
    m.on_change = changed
    await m.wait_ready(30)
    status = {s["name"]: s for s in m.status()}
    assert status["fakeext-srv"]["status"] == "disabled" and status["fakeext-srv"]["extension"] == "fakeext"

    # 打开（经扩展的状态，不写 mcp.json）
    store.set_settings("fakeext", {"token": "t"})
    await m.set_disabled("fakeext-srv", False)
    await m.wait_ready(30)
    assert any(t.full_name.startswith("mcp__fakeext-srv__") for t in m.tools())
    assert json.loads(mcp_json.read_text(encoding="utf-8")) == {"mcpServers": {}}

    # 「以后都不用问」记在扩展的状态里
    tool = next(t for t in m.tools() if not t.read_only)
    m.allow(tool.full_name)
    assert m.allowed(tool.full_name) and store.get("fakeext").always_allow["srv"] == [tool.name]
    assert m.forget("fakeext-srv", tool.name) and not m.allowed(tool.full_name)

    # 扩展的服务不能当普通服务删、改
    with pytest.raises(Exception, match="扩展"):
        await m.remove("fakeext-srv")

    # 卸载后 reload：服务停掉，对话那边跟着重连
    await m.stop_extension("fakeext")
    store.uninstall("fakeext")
    await m.reload()
    assert "fakeext-srv" not in {s["name"] for s in m.status()} and changes
    await m.stop_all()


def test_chat_side_sees_enabled_extension_servers(tmp_path: Path) -> None:
    from app.tools.mcp import load_config

    store = ExtensionStore(tmp_path / "exts")
    store.install(store.stage(fake_server_ext(tmp_path / "src"))[0])
    assert load_config(tmp_path / "none.json", store.mcp_servers()) == []  # 停着的不连
    store.set_settings("fakeext", {"token": "t"})
    store.set_server_disabled("fakeext", "srv", False)
    [cfg] = load_config(tmp_path / "none.json", store.mcp_servers())
    assert cfg.id == "fakeext-srv" and cfg.cwd == str(store.root / "fakeext") and cfg.env == {"FAKE_TOKEN": "t"}


# ---------- HTTP 接口 ----------


@pytest.mark.anyio
async def test_http_flow(tmp_path: Path) -> None:
    from app.deps import get_runtime
    from app.main import app

    src = make_ext(tmp_path / "src")
    rt = Runtime(tmp_path / "data", lambda: Scripted([["好"]]), shell=SHELL)
    app.dependency_overrides[get_runtime] = lambda: rt
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c:
            assert (await c.get("/extensions")).json()["extensions"] == []
            bad = await c.post("/extensions/inspect", json={"path": str(tmp_path / "nope")})
            assert bad.status_code == 400 and "找不到" in bad.json()["detail"]
            res = (await c.post("/extensions/inspect", json={"path": str(src)})).json()
            assert res["plan"]["id"] == "demo"
            installed = (await c.post("/extensions/install", json={"token": res["token"]})).json()
            assert installed["installed"] == "demo" and installed["extensions"][0]["commands"] == ["周报"]
            off = (await c.patch("/extensions/demo", json={"enabled": False})).json()
            assert off["extensions"][0]["enabled"] is False
            bad = await c.patch("/extensions/demo", json={"settings": {"x": "y"}})
            assert bad.status_code == 400
            gone = (await c.delete("/extensions/demo")).json()
            assert gone["extensions"] == []
            assert (await c.delete("/extensions/demo")).status_code == 400
    finally:
        app.dependency_overrides.pop(get_runtime, None)
        await rt.shutdown()
        shutil.rmtree(tmp_path / "data", ignore_errors=True)


# ---------- 终端 aila ext ----------


def test_cli_install_asks_first(tmp_path: Path) -> None:
    from app.cli.ext import _Local, main

    src = make_ext(tmp_path / "src")
    backend = _Local(ExtensionStore(tmp_path / "exts"))
    printed: list[str] = []
    assert main(["install", str(src)], ask=lambda _: "n", out=printed.append, backend=backend) == 0
    assert "加这些命令：/周报" in printed[0] and printed[-1] == "没装。" and backend.store.installed() == []
    assert main(["install", str(src)], ask=lambda _: "y", out=printed.append, backend=backend) == 0
    assert [e.id for e in backend.store.installed()] == ["demo"]
    main(["list"], out=printed.append, backend=backend)
    assert "demo  演示 1.0.0（启用）" in printed[-1]
    main(["disable", "demo"], out=printed.append, backend=backend)
    assert not backend.store.get("demo").enabled
    assert main(["remove", "nope"], out=printed.append, backend=backend) == 1 and "没成" in printed[-1]
    assert main(["remove", "demo"], out=printed.append, backend=backend) == 0 and backend.store.installed() == []
