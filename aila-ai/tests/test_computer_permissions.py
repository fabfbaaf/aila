"""电脑权限与文件工具验收。所有文件和规则都在临时目录，命令只判断，不操作真实电脑。"""

import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.confirm import Confirmations
from app.runtime.loop import Agent, AgentConfig
from app.runtime.permissions import COMPUTER_SCOPE, ComputerPolicy, Policy, Rules
from app.runtime.session import SessionStore
from app.runtime.snapshots import Snapshots
from app.runtime.tools import CodingTools
from app.runtime.workspace import ComputerWorkspace, Workspace, WorkspaceError

from .test_runtime import SHELL, Scripted, call


@pytest.fixture
def tmp_path() -> Iterator[Path]:
    # Windows 的 pytest 默认临时目录就在 AppData，普通文件用例需放在保护树之外。
    with tempfile.TemporaryDirectory(prefix=".computer-permissions-", dir=Path.cwd()) as folder:
        yield Path(folder)


@pytest.fixture
def computer(tmp_path: Path) -> tuple[ComputerPolicy, ComputerWorkspace]:
    home = tmp_path / "home"
    home.mkdir()
    data = tmp_path / "aila-ai" / "data"
    policy = ComputerPolicy(home, Rules(data / "computer_rules.json"), data_dir=data)
    workspace = ComputerWorkspace(home, data_dir=data)
    return policy, workspace


def test_computer_modes_and_separate_rules_scope(computer: tuple[ComputerPolicy, ComputerWorkspace]) -> None:
    policy, ws = computer
    with pytest.raises(ValueError, match="两种模式"):
        ComputerPolicy(ws.root, policy.rules, "auto")
    command = "Start-Process notepad"
    assert policy.decide("command", command).can_remember
    policy.remember("command", command)
    assert policy.decide("command", command).verdict == "allow"
    assert policy.rules.all() == {COMPUTER_SCOPE: [command]}
    assert Policy(ws.root, Rules(policy.rules.path.with_name("agent_rules.json"))).decide("command", command).verdict == "ask"
    chat = ComputerPolicy(ws.root, policy.rules, remember_commands=False)
    assert chat.decide("command", command).verdict == "ask"
    assert not chat.decide("command", command).can_remember


@pytest.mark.parametrize("command", [
    "Get-Location", "Get-Date", "Get-ChildItem Documents", "Get-Content Documents/note.txt",
    "Get-Process", "git status", "winget list", "node --version",
])
def test_obviously_readonly_commands_do_not_ask(
    computer: tuple[ComputerPolicy, ComputerWorkspace], command: str,
) -> None:
    policy, _ = computer
    for mode in ("ask", "read_only"):
        policy.mode = mode
        assert policy.decide("command", command).verdict == "allow"


@pytest.mark.parametrize("command", [
    "shutdown /s", "Restart-Computer", "Stop-Computer",
    "winget install Microsoft.PowerToys", "winget.exe uninstall SomeApp",
    "reg add HKLM/Software/Test", "Set-ItemProperty HKLM:/Software/Test -Name A -Value B",
    "Remove-Item Documents/old.txt", "Stop-Process -Id 100", "Start-Process notepad -Verb RunAs",
])
def test_dangerous_computer_commands_are_never_remembered(
    computer: tuple[ComputerPolicy, ComputerWorkspace], command: str,
) -> None:
    policy, _ = computer
    # 即使损坏的规则文件已经存过，也不能绕过危险判断。
    policy.rules.allow(COMPUTER_SCOPE, command)
    decision = policy.decide("command", command)
    assert decision.verdict == "ask" and not decision.can_remember
    policy.mode = "read_only"
    assert policy.decide("command", command).verdict == "deny"


@pytest.mark.parametrize("path", [
    "AppData/Roaming/ordinary.txt", ".ssh/config", ".gnupg/trustdb.gpg",
    "AppData/Local/Microsoft/Edge/User Data/Default/Cookies",
    "AppData/Local/Google/Chrome/User Data/Default/Login Data",
    "AppData/Roaming/Mozilla/Firefox/Profiles/test/logins.json",
    ".password-store/accounts.gpg", "Passwords/vault.kdbx", ".config/keepassxc/config.ini", ".env",
])
def test_protected_files_commands_and_edits_always_ask(
    computer: tuple[ComputerPolicy, ComputerWorkspace], path: str,
) -> None:
    policy, ws = computer
    target = ws.resolve(path)
    assert ws.is_secret(target)
    assert policy.decide("secret").verdict == "ask"
    policy.remember("edit")
    assert policy.decide("edit").verdict == "allow"
    protected = policy.decide("secret_edit")
    assert protected.verdict == "ask" and not protected.can_remember
    for command in (f'Get-Content "{target}"', f'Get-Content "{path}"'):
        policy.rules.allow(COMPUTER_SCOPE, command)
        decision = policy.decide("command", command)
        assert decision.verdict == "ask" and not decision.can_remember
        policy.remember("command", command)
        assert policy.decide("command", command).verdict == "ask"
    policy.mode = "read_only"
    assert policy.decide("secret").verdict == "ask"
    assert policy.decide("secret_edit").verdict == "deny"
    assert policy.decide("command", f'Get-Content "{target}"').verdict == "ask"


def test_runtime_data_env_expansions_and_indirect_paths(
    computer: tuple[ComputerPolicy, ComputerWorkspace], tmp_path: Path,
) -> None:
    policy, ws = computer
    for path in (tmp_path / "aila-ai/data/sessions/a.jsonl", tmp_path / "aila-ai/.env"):
        assert ws.is_secret(path)
        assert not policy.decide("command", f'Get-Content "{path}"').can_remember
    for command in (
        r'Get-Content "$HOME\.ssh/config"', r'Get-Content "$env:APPDATA/Mozilla/Firefox/logins.json"',
        r'Get-Content "%LOCALAPPDATA%/Microsoft/Edge/User Data/Default/Cookies"',
        'Get-Content $unresolved', 'Get-ChildItem -Recurse ~', 'Get-ChildItem -Recurse', 'Get-Content Documents/*',
    ):
        decision = policy.decide("command", command)
        assert decision.verdict == "ask" and not decision.can_remember
    external = tmp_path / "outside.txt"
    external.write_text("普通共享文件", encoding="utf-8")
    assert ws.resolve(str(external)) == external
    assert ws.resolve("~/Documents/note.txt") == ws.root / "Documents/note.txt"
    assert not ws.is_secret(external)
    external_appdata = tmp_path / "other" / "AppData" / "settings.txt"
    assert ws.is_secret(external_appdata)
    assert policy.decide("command", f'Get-Content "{external_appdata}"').verdict == "ask"
    with pytest.raises(WorkspaceError):
        Workspace(ws.root).resolve(str(external))


def test_protected_hardlinks_and_profile_symlinks(
    computer: tuple[ComputerPolicy, ComputerWorkspace], tmp_path: Path,
) -> None:
    policy, ws = computer
    outside = tmp_path / "secret.txt"
    outside.write_text("测试数据", encoding="utf-8")
    alias = ws.root / "alias.txt"
    alias.hardlink_to(outside)
    assert ws.is_secret(alias)
    decision = policy.decide("command", f'Get-Content "{alias}"')
    assert decision.verdict == "ask" and not decision.can_remember


async def invoke(agent: Agent, name: str, *, approve: bool = True, remember: bool = False,
                 **args: Any) -> list[dict[str, Any]]:
    events = []
    async for event in agent._run_tool(call(name, **args)):
        events.append(event)
        if event["type"] == "confirm":
            assert agent.confirms is not None
            agent.confirms.answer(event["id"], approve, remember)
    return events


def agent_for(policy: ComputerPolicy, workspace: ComputerWorkspace, tmp_path: Path) -> Agent:
    session = SessionStore(tmp_path / "sessions").create(workspace.root, "ask", "test", "", {"kind": "computer"})
    return Agent(
        session, Scripted([["测试。"]]), CodingTools(workspace, SHELL, snapshots=Snapshots(session.path.with_suffix(".snap"))),
        policy, Confirmations(timeout=1), AgentConfig(),
    )


@pytest.mark.anyio
async def test_computer_tools_ask_before_protected_reads_and_never_apply_edit_allowance(
    computer: tuple[ComputerPolicy, ComputerWorkspace], tmp_path: Path,
) -> None:
    policy, ws = computer
    protected = ws.root / "AppData" / "plain.txt"
    protected.parent.mkdir()
    protected.write_text("受保护文本", encoding="utf-8")
    ordinary = ws.root / "note.txt"
    ordinary.write_text("普通文本", encoding="utf-8")
    agent = agent_for(policy, ws, tmp_path)
    assert not [e for e in await invoke(agent, "read_file", path="note.txt") if e["type"] == "confirm"]
    refused = await invoke(agent, "read_file", path=str(protected), approve=False)
    assert refused[-1]["status"] == "rejected" and protected not in ws.seen
    approved = await invoke(agent, "read_file", path=str(protected))
    confirmation, = [e for e in approved if e["type"] == "confirm"]
    assert confirmation["kind"] == "secret" and not confirmation["can_remember"]
    assert "受保护文本" in approved[-1]["content"]
    edited = await invoke(agent, "edit_file", path="note.txt", old_string="普通", new_string="新", remember=True)
    assert edited[-1]["status"] == "ok" and policy.edits_allowed
    secret_edit = await invoke(agent, "edit_file", path=str(protected), old_string="受保护", new_string="新")
    dialog, = [e for e in secret_edit if e["type"] == "confirm"]
    assert dialog["kind"] == "secret_edit" and not dialog["can_remember"]
    assert secret_edit[-1]["status"] == "ok"
    assert "snap" not in secret_edit[-1]
    policy.mode = "read_only"
    refused = await invoke(agent, "write_file", path=str(protected), content="不能写")
    assert refused[-1]["status"] == "denied"


@pytest.mark.anyio
async def test_default_search_excludes_protected_tree_and_explicit_search_confirms(
    computer: tuple[ComputerPolicy, ComputerWorkspace], tmp_path: Path,
) -> None:
    policy, ws = computer
    (ws.root / "AppData").mkdir()
    (ws.root / "AppData" / "record.txt").write_text("needle protected", encoding="utf-8")
    (ws.root / "record.txt").write_text("needle ordinary", encoding="utf-8")
    agent = agent_for(policy, ws, tmp_path)
    found = await invoke(agent, "search", pattern="needle")
    assert "needle ordinary" in found[-1]["content"] and "needle protected" not in found[-1]["content"]
    listed = await invoke(agent, "list_files")
    assert "AppData" not in listed[-1]["content"]
    explicit = await invoke(agent, "search", path="AppData", pattern="needle")
    assert [e for e in explicit if e["type"] == "confirm"]
    assert "needle protected" in explicit[-1]["content"]
    explicit_list = await invoke(agent, "list_files", path="AppData")
    assert [e for e in explicit_list if e["type"] == "confirm"]


@pytest.mark.anyio
async def test_external_file_edit_leaves_snapshot_and_can_undo(
    computer: tuple[ComputerPolicy, ComputerWorkspace], tmp_path: Path,
) -> None:
    policy, ws = computer
    path = tmp_path / "shared" / "note.txt"
    path.parent.mkdir()
    path.write_bytes(b"before\r\n")
    agent = agent_for(policy, ws, tmp_path)
    await invoke(agent, "read_file", path=str(path))
    edited = await invoke(agent, "edit_file", path=str(path), old_string="before", new_string="after")
    result = edited[-1]
    assert result["status"] == "ok" and result["file"] == path.as_posix() and type(result["snap"]) is int
    assert agent.tools.snapshots is not None
    restored, skipped = agent.tools.snapshots.restore(ws, [result])
    assert restored == [path.as_posix()] and not skipped and path.read_bytes() == b"before\r\n"
    assert Snapshots._target(ws, "note.txt") == ws.root / "note.txt"
    with pytest.raises(WorkspaceError):
        Snapshots._target(ws, "../not-from-tool.txt")
    with pytest.raises(WorkspaceError):
        Snapshots._target(ws, str(ws.root / "AppData/record.txt"))


def test_computer_command_preview_uses_command_text(
    computer: tuple[ComputerPolicy, ComputerWorkspace],
) -> None:
    _, ws = computer
    tools = CodingTools(ws, SHELL)
    args = {"command": "Restart-Computer", "description": "只是看一下"}
    tool = tools.tools["run_command"]
    assert tool.preview is not None and "Restart-Computer" in tool.preview(args)
    assert "只是看一下" not in tool.preview(args) and "只是看一下" not in tool.status(args)


def test_confirmation_recheck_keeps_original_path_binding(
    computer: tuple[ComputerPolicy, ComputerWorkspace], monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, ws = computer
    tools = CodingTools(ws, SHELL)
    args = {"path": "note.txt", "content": "新内容"}
    tool = tools.tools["write_file"]
    assert tool.kind(args) == "edit"
    target = ws.root / "AppData" / "different.txt"
    monkeypatch.setattr(ws, "resolve", lambda *a, **k: target)
    with pytest.raises(WorkspaceError, match="保护状态变了"):
        tool.kind(args)
