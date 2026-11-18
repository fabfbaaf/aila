"""电脑概况和对话入口用临时目录及假执行器验收，不运行真实电脑命令。"""

import json
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import computer, deps
from app.llm import ToolCall
from app.main import app
from app.runtime.permissions import ComputerPolicy, Rules
from app.runtime.shell import CommandResult
from app.tools import EMIT, SkillRegistry, Toolbox
from app.tools import computer as computer_tools


def tool_call(name: str, **args) -> ToolCall:
    return ToolCall("computer-demo", name, json.dumps(args, ensure_ascii=False))


def test_inventory_names_only_and_optional_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    menu = tmp_path / "Programs"
    menu.mkdir()
    (menu / "记事本.lnk").write_text("这段内容绝对不应读取", encoding="utf-8")
    (menu / "说明.txt").write_text("其他文件也不读", encoding="utf-8")
    for noise in ("Uninstall Node.js", "Node.js documentation", "Node.js website", "卸载 某软件", "某软件 帮助"):
        (menu / f"{noise}.lnk").write_text("", encoding="utf-8")
    monkeypatch.setattr(computer, "program_directories", lambda _: [menu])
    monkeypatch.setattr(computer.shutil, "which", lambda name: "C:/工具/bin/" + name if name in ("git", "node", "excel") else None)
    context = computer.build_computer_context(tmp_path, "PowerShell")
    assert "记事本" in context and "git" in context and "node" in context and "excel" in context
    assert "这段内容绝对" not in context and "说明.txt" not in context and "C:/工具" not in context
    assert "Node.js" not in context and "某软件" not in context  # 卸载、文档、官网、帮助不算程序
    assert str(tmp_path / "Downloads") in context and "先列出要动哪些文件" in context
    assert len(computer.WINDOWS_GUIDE) < 1500

    def unexpected_probe(_):
        raise AssertionError("关掉开关时不探测程序")

    monkeypatch.setattr(computer, "inventory_names", unexpected_probe)
    hidden = computer.build_computer_context(tmp_path, "PowerShell", False)
    assert "开始菜单中的程序" not in hidden and "PATH 中的命令行工具" not in hidden
    assert str(tmp_path) in hidden and "系统：" in hidden


def test_failed_and_slow_probe_does_not_block_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    release = threading.Event()
    monkeypatch.setattr(computer, "PROBE_SECONDS", 0.02)
    monkeypatch.setattr(computer.shutil, "which", lambda _: release.wait(1) and None)
    start = time.monotonic()
    try:
        context = computer.build_computer_context(tmp_path, "PowerShell")
        assert time.monotonic() - start < 0.5
        assert "暂未探测到" in context
    finally:
        release.set()

    def failed(_):
        raise OSError("模拟目录不可用")

    monkeypatch.setattr(computer, "inventory_names", failed)
    assert "用户目录：" in computer.build_computer_context(tmp_path, "PowerShell")


def test_computer_inventory_setting_and_preview(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(deps, "settings", replace(deps.settings, computer_inventory=True))
    monkeypatch.setattr(computer, "inventory_names", lambda _: (["临时程序"], ["git"]))
    client = TestClient(app)
    assert client.get("/settings").json()["computer"]["inventory"] is True
    assert "临时程序" in client.get("/agent/computer/context").json()["context"]
    with monkeypatch.context() as local:
        local.setenv("AILA_COMPUTER_INVENTORY", "on")
        changed = client.put("/settings", json={"computerInventory": False})
        assert changed.status_code == 200 and "computer" in changed.json()["reloaded"]
        assert changed.json()["settings"]["computer"]["inventory"] is False
        preview = client.get("/agent/computer/context").json()
        assert preview["inventory"] is False and "临时程序" not in preview["context"]
    assert client.put("/settings", json={"computerInventory": "false"}).status_code == 422
    assert client.get("/agent/computer/context", headers={"Origin": "https://example.org"}).status_code == 403


@pytest.mark.anyio
async def test_chat_command_requires_confirmation_and_ignores_task_allow_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    seen = []

    async def execute(shell, command, cwd, timeout):
        seen.append((shell.kind, command, cwd))
        return CommandResult(0, "假执行器结果", 0.01, False)

    monkeypatch.setattr(computer_tools, "run_command", execute)
    monkeypatch.setattr(computer_tools, "computer_shell", lambda: SimpleNamespace(kind="powershell"))
    registry = SkillRegistry([computer_tools.computer_skill()])
    readonly = tool_call("computer_command", command="Get-Location")
    assert "关掉" in await registry.toolbox().run(readonly)
    box = registry.toolbox({"computer": True})
    assert box.confirmation(readonly) is None
    assert "假执行器结果" in await box.run(readonly)

    command = "Start-Process notepad.exe"
    rules = Rules(deps.settings.data_dir / "computer_rules.json")
    policy = ComputerPolicy(tmp_path, rules, data_dir=deps.settings.data_dir, env_file=deps.ENV_FILE)
    policy.remember("command", command)
    for text in (command, "Restart-Computer", "winget install --id Fake.Program",
                 'Get-Content "$env:APPDATA/test.txt"'):
        call = tool_call("computer_command", command=text)
        ask = box.confirmation(call)
        assert ask is not None and ask.detail == text
        before = len(seen)
        assert "确认" in await box.run(call) and len(seen) == before
        assert "假执行器结果" in await box.run(call, approved=True)
    assert all(kind == "powershell" and cwd == tmp_path for kind, _, cwd in seen)
    direct = registry.toolbox({"computer": True})._tools["computer_command"]
    assert "确认" in await direct.run({"command": command, "approved": True})


@pytest.mark.anyio
async def test_chat_computer_handoff_confirms_then_starts_a_plan() -> None:
    calls = []

    class FakeRuntime:
        def create(self, **kwargs):
            calls.append(("create", kwargs))
            return SimpleNamespace(id="computer-session")

        def start(self, sid, goal, **kwargs):
            calls.append(("start", sid, goal, kwargs))

    skill = computer_tools.computer_task_skill(lambda: FakeRuntime())
    box = Toolbox(skill.tools)
    task = tool_call("computer_task", task="把测试文件夹按类型整理")
    ask = box.confirmation(task)
    assert ask is not None and "电脑任务" in ask.action
    assert "确认" in await box.run(task) and calls == []
    events = []
    token = EMIT.set(events.append)
    try:
        assert "出" in await box.run(task, approved=True)
    finally:
        EMIT.reset(token)
    assert calls == [
        ("create", {"kind": "computer", "mode": "ask"}),
        ("start", "computer-session", "把测试文件夹按类型整理", {"plan": True}),
    ]
    assert events == [{"type": "agent_task", "session": "computer-session", "project": "电脑", "kind": "computer"}]
