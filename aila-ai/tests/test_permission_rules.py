"""记住的命令：只读列表、按原文撤销，以及接口和两种终端后端的即时生效。"""

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
from app.runtime.permissions import Policy, Rules
from app.runtime.runner import Runtime

from .test_runtime import SHELL, Scripted


@pytest.fixture
def runtime(tmp_path: Path) -> Runtime:
    return Runtime(tmp_path / "data", lambda: Scripted([["好"]]), shell=SHELL)


@pytest.fixture
def client(runtime: Runtime):
    app.dependency_overrides[get_runtime] = lambda: runtime
    client = TestClient(app)
    try:
        yield client
    finally:
        client.close()
        app.dependency_overrides.pop(get_runtime, None)


def test_all_is_read_only_and_returns_independent_lists(tmp_path: Path) -> None:
    path = tmp_path / "rules.json"
    rules = Rules(path)
    assert rules.all() == {} and not path.exists()
    project = tmp_path / "项目"
    rules.allow(project, 'npm test -- --grep "a b"')
    before = path.read_bytes()
    snapshot = rules.all()
    snapshot[str(project)].append("不会写回")
    snapshot.clear()
    assert rules.all() == {str(project): ['npm test -- --grep "a b"']}
    assert path.read_bytes() == before


def test_all_handles_missing_invalid_and_malformed_data(tmp_path: Path) -> None:
    path = tmp_path / "rules.json"
    rules = Rules(path)
    for content in ("{", "[]", "null"):
        path.write_text(content, encoding="utf-8")
        assert rules.all() == {}
    path.write_text(json.dumps({"bad": "not-a-list", "project": ["npm test", 12, None]}), encoding="utf-8")
    assert rules.all() == {"project": ["npm test"]}


def test_rules_api_lists_sorted_projects_and_preserves_exact_commands(client: TestClient, runtime: Runtime, tmp_path: Path) -> None:
    a, z = tmp_path / "a", tmp_path / "z"
    runtime.rules.allow(z, "npm run build")
    runtime.rules.allow(a, 'npm test -- --grep "a b"')
    runtime.rules.allow(a, "npm test ")
    assert client.get("/agent/rules").json() == [
        {"project": str(a), "commands": ['npm test -- --grep "a b"', "npm test "]},
        {"project": str(z), "commands": ["npm run build"]},
    ]


def test_deleting_rule_immediately_changes_existing_policy(client: TestClient, runtime: Runtime, tmp_path: Path) -> None:
    project, other = tmp_path / "project", tmp_path / "other"
    policy = Policy(project, runtime.rules, "ask")
    runtime.rules.allow(project, "npm test")
    runtime.rules.allow(project, "npm run build")
    runtime.rules.allow(other, "npm test")
    assert policy.decide("command", "npm test").verdict == "allow"
    res = client.request("DELETE", "/agent/rules", json={"project": str(project), "command": "npm test"})
    assert res.status_code == 200 and res.json() == {"ok": True}
    assert policy.decide("command", "npm test").verdict == "ask"
    assert policy.decide("command", "npm run build").verdict == "allow"
    assert runtime.rules.commands(other) == ["npm test"]
    assert client.request("DELETE", "/agent/rules", json={"project": str(project), "command": "npm test"}).status_code == 404
    assert client.request("DELETE", "/agent/rules", json={"project": str(other), "command": "npm test "}).status_code == 404
    assert runtime.rules.commands(other) == ["npm test"]


@pytest.mark.parametrize("body", [{}, {"project": "x", "command": ""}, {"project": 12, "command": "npm test"}])
def test_delete_validates_request(client: TestClient, body: dict) -> None:
    assert client.request("DELETE", "/agent/rules", json=body).status_code == 422


def test_rules_api_rejects_remote_origins(client: TestClient) -> None:
    headers = {"origin": "https://example.com"}
    assert client.get("/agent/rules", headers=headers).status_code == 403
    assert client.request("DELETE", "/agent/rules", headers=headers, json={"project": "x", "command": "npm test"}).status_code == 403


@pytest.mark.anyio
@pytest.mark.parametrize("backend_kind", ["embedded", "http"])
async def test_terminal_lists_current_project_and_forgets_by_number(runtime: Runtime, tmp_path: Path, backend_kind: str) -> None:
    root = tmp_path / "project"
    root.mkdir()
    runtime.rules.allow(root, "npm test")
    runtime.rules.allow(root, 'npm run build -- --label "[green]原文[/green]"')
    runtime.rules.allow(tmp_path / "other", "other-project-command")
    app.dependency_overrides[get_runtime] = lambda: runtime
    backend = EmbeddedBackend(runtime) if backend_kind == "embedded" else HttpBackend("http://testserver", transport=httpx.ASGITransport(app=app))
    console = Console(file=io.StringIO(), record=True, width=160, force_terminal=False, color_system=None)
    try:
        info = await backend.create(str(root), "ask")
        policy = runtime.agent(info["id"]).policy
        with create_pipe_input() as inp:
            term = Terminal(backend, console, info, inp=inp, output=DummyOutput())
            await term.command("/rules")
            output = console.export_text(clear=True)
            assert "1. npm test" in output and '2. npm run build -- --label "[green]原文[/green]"' in output
            assert "other-project-command" not in output
            for invalid in ("/rules forget 0", "/rules forget 999", "/rules forget x", "/rules other 1", "/rules forget 1 extra"):
                await term.command(invalid)
                assert len(runtime.rules.commands(root)) == 2
            await term.command("/rules forget 1")
            assert policy.decide("command", "npm test").verdict == "ask"
            assert runtime.rules.commands(root) == ['npm run build -- --label "[green]原文[/green]"']
            await term.command("/rules forget 1")
            await term.command("/rules")
            assert "还没有记住的命令" in console.export_text()
        with pytest.raises(BackendError, match="没有这条"):
            await backend.forget_rule(str(root), "npm test")
        assert (await backend.rules())[-1]["project"] in (str(root), str(tmp_path / "other"))
        assert runtime.rules.commands(tmp_path / "other") == ["other-project-command"]
    finally:
        app.dependency_overrides.pop(get_runtime, None)
        await backend.close()
        await runtime.shutdown()
