"""按本机 Agent 分组显示模型，以及跨 Agent 的任务交接。"""

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from app.runtime import localagents
from app.runtime.catalog import ModelCatalog
from app.runtime.external import ExternalAgent
from app.runtime.handoff import recap
from app.runtime.localagents import LocalAgent
from app.runtime.permissions import Policy, Rules
from app.runtime.runner import Runtime, RuntimeError_
from app.runtime.session import SessionStore

from .test_external_agent import FakeConn
from .test_runtime import SHELL, Scripted


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in ("AILA_NODE", "AILA_NPM_CLI", "AILA_ACP_BUNDLED", "AILA_ACP_DIR"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def adapter(root: Path, engine: str) -> Path:
    script = root / "node_modules" / localagents.ENGINES[engine].script
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("")
    return script


# ---------- 艾拉自带的 Node、安装包里的适配器、自动装 ----------


def test_bundled_node_is_preferred_and_runs_as_node(tmp_path: Path, clean_env: pytest.MonkeyPatch) -> None:
    app = tmp_path / "艾拉.exe"
    app.write_text("")
    clean_env.setenv("AILA_NODE", str(app))
    assert localagents.node_runtime(which=lambda n: "C:/node.exe") == (str(app), {"ELECTRON_RUN_AS_NODE": "1"})
    clean_env.delenv("AILA_NODE")
    assert localagents.node_runtime(which=lambda n: "C:/node.exe" if n == "node" else None) == ("C:/node.exe", {})  # 从源码跑：电脑上的
    assert localagents.node_runtime(which=lambda n: None) is None


def test_bundled_npm_runs_on_the_bundled_node(tmp_path: Path, clean_env: pytest.MonkeyPatch) -> None:
    app, cli = tmp_path / "艾拉.exe", tmp_path / "npm-cli.js"
    app.write_text(""), cli.write_text("")
    clean_env.setenv("AILA_NODE", str(app))
    clean_env.setenv("AILA_NPM_CLI", str(cli))
    assert localagents.npm_command(which=lambda n: None) == ([str(app), str(cli)], {"ELECTRON_RUN_AS_NODE": "1"})


def test_codex_adapter_from_resources_runs_on_electron_node(tmp_path: Path, clean_env: pytest.MonkeyPatch) -> None:
    app = tmp_path / "艾拉.exe"
    app.write_text("")
    bundled = tmp_path / "resources" / "acp"
    script = adapter(bundled, "codex")
    clean_env.setenv("AILA_NODE", str(app))
    clean_env.setenv("AILA_ACP_BUNDLED", str(bundled))
    clean_env.setenv("ELECTRON_RUN_AS_NODE", "1")  # 艾拉自己在跑时可能带着：起它自己会说 ACP 的程序时要去掉
    node, args, env = localagents.launch("codex", tmp_path / "data", which=lambda n: "C:/codex.exe" if n == "codex" else None, local_app_data="")
    assert node == str(app) and args == [str(script)] and env["ELECTRON_RUN_AS_NODE"] == "1" and env["CODEX_PATH"] == str(Path("C:/codex.exe"))
    with pytest.raises(localagents.AdapterMissing):  # 测试的资源目录只有 Codex
        localagents.launch("claude", tmp_path / "data", which=lambda n: "C:/claude.exe" if n == "claude" else None, local_app_data="")
    _prog, _args, env = localagents.launch("codebuddy", tmp_path / "data", which=lambda n: "C:/cb.cmd" if n == "codebuddy" else None,
                               local_app_data="")
    assert "ELECTRON_RUN_AS_NODE" not in env


def test_second_account_launch_and_detection_are_isolated(tmp_path: Path, clean_env: pytest.MonkeyPatch) -> None:
    program = {"codex": "C:/codex.exe", "claude": "C:/claude.exe", "node": "C:/node.exe"}.get
    clean_env.setenv("CODEX_HOME", str(tmp_path / "main-codex"))
    clean_env.setenv("CODEX_ACCESS_TOKEN", "never-passed-to-second-codex")
    clean_env.setenv("CLAUDE_CODE_OAUTH_TOKEN", "never-passed-to-second-claude")
    data = tmp_path / "data"
    adapter(data / "acp", "codex")
    adapter(data / "acp", "claude")
    seen = {}

    def run(args, *, env=None):
        if args[-1] == "--version":
            return 0, "1.2.3"
        if env is not None:
            seen[Path(args[0]).stem] = env
        return (0, '{"loggedIn": true}') if Path(args[0]).stem == "claude" else (0, "Logged in using ChatGPT")

    def detected(**kwargs):
        return {a.id: a for a in localagents.detect(data, which=program, run=run, local_app_data="", **kwargs)}

    assert "codex-alt" not in detected()
    assert detected(include_profiles=True)["codex-alt"].logged_in is False
    for engine, key, token_key in [
        ("codex", "CODEX_HOME", "CODEX_ACCESS_TOKEN"),
        ("claude", "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN"),
    ]:
        slot = f"{engine}-alt"
        with pytest.raises(localagents.LocalAgentError, match="第二个账号"):
            localagents.launch(slot, data, which=program, local_app_data="")
        home = localagents.account_home(slot, data)
        assert home is not None
        home.mkdir(parents=True)
        _, _, env = localagents.launch(slot, data, which=program, local_app_data="")
        assert env[key] == str(home) and token_key not in env
        assert detected()[slot].logged_in is True
        assert seen[engine][key] == str(home) and token_key not in seen[engine]
    assert localagents.launch("codex", data, which=program, local_app_data="")[2]["CODEX_HOME"] == str(tmp_path / "main-codex")


def test_missing_claude_adapter_is_not_a_blocker_when_it_can_be_installed(tmp_path: Path, clean_env: pytest.MonkeyPatch) -> None:
    app, cli = tmp_path / "艾拉.exe", tmp_path / "npm-cli.js"
    app.write_text(""), cli.write_text("")
    clean_env.setenv("AILA_NODE", str(app))
    clean_env.setenv("AILA_NPM_CLI", str(cli))
    paths = {"claude": "C:/claude.exe"}

    def run(args: list[str], timeout: float = 0) -> tuple[int, str]:
        return (0, "2.1\n") if args[1:] == ["--version"] else (0, '{"loggedIn": true}')

    found = {a.id: a for a in localagents.detect(tmp_path, which=paths.get, run=run, local_app_data="")}
    claude = found["claude"]
    assert claude.ready and claude.installs_adapter and not claude.problem and claude.can_login and claude.account == "Claude"
    clean_env.delenv("AILA_NPM_CLI")
    claude = {a.id: a for a in localagents.detect(tmp_path, which=paths.get, run=run, local_app_data="")}["claude"]
    assert not claude.ready and "npm" in claude.problem  # 装不了：说清楚


def test_login_command_is_the_official_one(clean_env: pytest.MonkeyPatch) -> None:
    which = {"codex": "C:/codex.exe", "claude": "C:/claude.exe", "agy": "C:/agy.exe"}.get
    assert localagents.login_command("codex", which=which, local_app_data="") == [str(Path("C:/codex.exe")), "login"]
    assert localagents.login_command("claude", which=which, local_app_data="") == [str(Path("C:/claude.exe")), "auth", "login"]
    assert localagents.login_command("antigravity", which=which, local_app_data="") == [str(Path("C:/agy.exe"))]


def test_provider_process_keeps_own_configuration_without_aila_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "aila-fixture")
    monkeypatch.setenv("SEARCH_API_KEY", "search-fixture")
    monkeypatch.setenv("AILA_MAIL_PASS", "mail-fixture")
    monkeypatch.setenv("CURSOR_API_KEY", "vendor-fixture")
    _, _, env = localagents.launch("cursor", tmp_path, which=lambda name: "C:/cursor-agent.exe", local_app_data="")
    assert env["CURSOR_API_KEY"] == "vendor-fixture"
    assert not {"LLM_API_KEY", "SEARCH_API_KEY", "AILA_MAIL_PASS"} & env.keys()


def test_first_use_installs_the_adapter_then_starts(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    session = SessionStore(tmp_path / "sessions").create(root, "ask", "Claude Code", "", {"engine": "claude"})
    installed: list[list[str]] = []
    state = {"installed": False}

    def launcher(engine: str, data_dir: Path) -> tuple[str, list[str], dict[str, str]]:
        if not state["installed"]:
            raise localagents.AdapterMissing("还没装")
        return "node", ["adapter.js"], {}

    async def installer(data_dir: Path, engines: list[str] | None = None) -> str:
        installed.append(engines or [])
        state["installed"] = True
        return "装好了"

    @asynccontextmanager
    async def spawn(factory, *a: Any, **kw: Any):
        yield FakeConn(factory, [("say", "好")]), None

    policy = Policy(root, Rules(tmp_path / "rules.json"), "ask")
    agent = ExternalAgent(session, "claude", policy, None, tmp_path, spawn=spawn, launcher=launcher, installer=installer)

    async def go() -> list[dict]:
        return [e async for e in agent.turn("你好")]

    events = asyncio.run(go())
    assert installed == [["claude"]]
    assert any(e["type"] == "note" and "适配器" in e["text"] for e in events)
    assert events[-1]["type"] == "turn_end" and events[-1]["reason"] == "done"


def own(use: str) -> dict:
    return {"engine": "aila", "title": "艾拉", "ready": True, "models": [{"value": "", "name": "deepseek" if use == "chat" else "coder"}]}


def agents() -> list[LocalAgent]:
    return [LocalAgent("codex", "Codex", path="C:/codex.exe", ready=True, logged_in=True),
            LocalAgent("claude", "Claude Code", path="C:/claude.exe", ready=False, logged_in=False,
                       problem="Claude Code 还没登录")]


def test_catalog_groups_by_account_and_asks_models_in_the_background(tmp_path: Path) -> None:
    asked: list[str] = []

    async def prober(engine: str, data_dir: Path) -> list[dict]:
        asked.append(engine)
        return [{"value": "gpt-6.2", "name": "GPT-6.2", "default": True}, {"value": "gpt-6-luna", "name": "GPT-6 Luna"}]

    async def go() -> tuple[list[dict], list[dict], list[dict]]:
        catalog = ModelCatalog(tmp_path, own, detector=lambda d: agents(), prober=prober)
        first = await catalog.groups("chat")
        await catalog.wait()
        second = await catalog.groups("task")
        third = await ModelCatalog(tmp_path, own, detector=lambda d: agents(), prober=prober).groups()  # 重启以后：用记下的
        return first, second, third

    first, second, third = asyncio.run(go())
    assert [g["engine"] for g in first] == ["aila", "codex", "claude"]
    assert first[0]["models"][0]["name"] == "deepseek" and second[0]["models"][0]["name"] == "coder"  # 对话、编程各用各的
    assert first[1]["loading"] and first[1]["models"][0]["value"] == ""  # 还在问：先给「默认模型」
    assert [m["value"] for m in second[1]["models"]] == ["gpt-6.2", "gpt-6-luna"] and not second[1]["loading"]
    claude = second[2]
    assert not claude["ready"] and "登录" in claude["problem"]
    assert asked == ["codex"]  # 没登录的不去问
    assert [m["value"] for m in third[1]["models"]] == ["gpt-6.2", "gpt-6-luna"] and not third[1]["loading"]
    assert json.loads((tmp_path / "models.json").read_text(encoding="utf-8"))["codex"]["models"][0]["value"] == "gpt-6.2"


def test_catalog_keeps_working_when_asking_fails(tmp_path: Path) -> None:
    async def prober(engine: str, data_dir: Path) -> list[dict]:
        raise localagents.LocalAgentError("额度用完了")

    async def go() -> list[dict]:
        catalog = ModelCatalog(tmp_path, own, detector=lambda d: agents(), prober=prober)
        await catalog.groups()
        await catalog.wait()
        return await catalog.groups()

    codex = asyncio.run(go())[1]
    assert "额度用完了" in codex["problem"] and codex["models"][0]["value"] == ""


def test_six_more_agents_are_available_for_tasks_only(tmp_path: Path) -> None:
    def inventory(_folder: Path) -> list[LocalAgent]:
        return [
            LocalAgent(engine.id, engine.title, path=f"C:/tools/{engine.command}.exe", ready=True,
                       logged_in=None, can_login=True, account=engine.account)
            for engine in localagents.ENGINES.values() if engine.id != "minimax"
        ] + [LocalAgent("minimax", "MiniMax Code")]

    async def go() -> None:
        async def probe(_engine: str, _folder: Path) -> list[dict]:
            return [{"value": "vendor/model+variant", "name": "Model"}]

        catalog = ModelCatalog(tmp_path, own, detector=inventory, prober=probe)
        try:
            chat = await catalog.groups("chat")
            assert {g["engine"] for g in chat} == {"aila", "codex", "claude", "antigravity", "codebuddy"}
            task = await catalog.groups("task")
            assert {g["engine"] for g in task} == {"aila", *localagents.ENGINES}
            cursor = next(g for g in task if g["engine"] == "cursor")
            assert cursor["can_login"] is True and cursor["can_install"] is False
            assert task[-1]["engine"] == "minimax" and task[-1]["can_install"] is True
        finally:
            await catalog.close()

    asyncio.run(go())


def test_own_group_says_when_there_is_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import deps

    group = deps.own_models("chat")
    assert group["engine"] == "aila" and group["models"][0]["value"] == ""
    if deps.settings.mock:
        assert not group["ready"] and "密钥" in group["problem"]


# ---------- 换别家的模型接着做 ----------


def test_recap_is_built_from_the_log() -> None:
    events = [
        {"type": "start"},
        {"type": "user", "text": "给 calc.py 加一个减法"},
        {"type": "assistant", "text": "好，我先看看代码。"},
        {"type": "tool", "name": "edit_file", "status": "ok", "file": "calc.py"},
        {"type": "tool", "name": "edit_file", "status": "error", "file": "broken.py"},
        {"type": "tool_start", "command": "pytest -q"},
        {"type": "assistant", "text": "加好了，测试还有一个没过。"},
        {"type": "turn_end", "reason": "interrupted"},
    ]
    text = recap(events, "Codex（gpt-6-luna）")
    assert "Codex（gpt-6-luna）" in text and "给 calc.py 加一个减法" in text and "测试还有一个没过" in text
    assert "改过的文件：calc.py" in text and "broken.py" not in text  # 没改成的不算
    assert "pytest -q" in text and "被你打断了" in text


def make_runtime(tmp_path: Path) -> tuple[Runtime, Path]:
    project = tmp_path / "proj"
    project.mkdir()
    return Runtime(tmp_path / "data", lambda: Scripted([["好的。"]]), shell=SHELL), project


def test_handoff_opens_a_new_task_with_the_recap(tmp_path: Path) -> None:
    rt, project = make_runtime(tmp_path)
    old = rt.create(str(project), "ask", engine="codex")
    old.append({"type": "engine_config", "id": "model", "value": "gpt-6-luna"})
    old.append({"type": "user", "text": "给 calc.py 加一个减法"})
    old.append({"type": "assistant", "text": "加好了。"})
    old.append({"type": "turn_end", "reason": "done"})
    old.append({"type": "title", "title": "加减法"})

    new = rt.handoff(old.id, "claude", "Claude Sonnet")
    assert new.events[0]["engine"] == "claude" and new.cwd == old.cwd and new.mode == "ask"
    handoff = next(e for e in new.events if e["type"] == "handoff_in")
    assert handoff["from"] == old.id and "Codex（gpt-6-luna）" in handoff["recap"] and "给 calc.py 加一个减法" in handoff["recap"]
    assert new.title == "加减法"
    old = rt.store.get(old.id)
    assert old.summary()["handed_to"] == new.id and new.summary()["handed_from"] == old.id


def test_handoff_to_aila_puts_the_recap_in_her_context(tmp_path: Path) -> None:
    rt, project = make_runtime(tmp_path)
    old = rt.create(str(project), "ask", engine="codex")
    old.append({"type": "user", "text": "修一下登录的 bug"})
    new = rt.handoff(old.id, None, "deepseek")
    assert new.events[0].get("engine") is None and "修一下登录的 bug" in new.events[0]["context"]


@pytest.mark.parametrize("target", ["claude", "codex-alt"])
def test_handoff_moves_the_branch_to_the_new_task(tmp_path: Path, target: str) -> None:
    rt, project = make_runtime(tmp_path)
    branch = {"repo": str(project), "worktree": str(tmp_path / "wt"), "branch": "aila/x", "base": "abc", "sub": "", "links": []}
    old = rt.store.create(project, "ask", "Codex", "", {"engine": "codex", "branch": branch})
    home = localagents.account_home(target, rt.data_dir)
    if home is not None:
        with pytest.raises(RuntimeError_, match="第二个账号"):
            rt.handoff(old.id, target)
        assert rt.store.get(old.id).branch == branch
        home.mkdir(parents=True)
    new = rt.handoff(old.id, target)
    assert new.events[0]["engine"] == target
    assert new.branch["branch"] == "aila/x"
    assert rt.store.get(old.id).branch is None  # 原来的任务不再合并、放弃、删这个分支
    with pytest.raises(RuntimeError_, match="工作区已经交给新任务"):
        rt.start(old.id, "继续修改")


def test_handoff_refuses_while_running_or_for_computer_tasks(tmp_path: Path) -> None:
    rt, project = make_runtime(tmp_path)
    computer = rt.create(None, "ask", kind="computer")
    with pytest.raises(RuntimeError_, match="电脑任务"):
        rt.handoff(computer.id, "codex")
    with pytest.raises(RuntimeError_, match="没有这个会话"):
        rt.handoff("nope", "codex")


def test_external_agent_gets_the_recap_only_in_its_first_prompt(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    session = SessionStore(tmp_path / "sessions").create(root, "ask", "Claude Code", "", {"engine": "claude"})
    session.append({"type": "handoff_in", "from": "x", "recap": "（接手说明：前情）"})
    conns: list[FakeConn] = []

    @asynccontextmanager
    async def spawn(factory, *a: Any, **kw: Any):
        conn = FakeConn(factory, [("say", "好")])
        conns.append(conn)
        yield conn, None

    agent = ExternalAgent(session, "claude", Policy(root, Rules(tmp_path / "rules.json"), "ask"), None, tmp_path,
                          spawn=spawn, launcher=lambda e, d: ("node", ["a.js"], {}))

    async def go() -> None:
        [e async for e in agent.turn("接着做")]
        [e async for e in agent.turn("再加个乘法")]

    asyncio.run(go())
    prompts = [arg for kind, arg in conns[0].calls if kind == "prompt"]
    assert prompts[0].startswith("（接手说明：前情）") and prompts[0].endswith("接着做")
    assert prompts[1] == "再加个乘法"
    users = [e["text"] for e in session.events if e["type"] == "user"]
    assert users == ["接着做", "再加个乘法"]  # 任务页上只显示你说的
