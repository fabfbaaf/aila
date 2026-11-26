"""项目资料的作用域、两跳代码查找和可核对的引用。"""

import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import deps
from app.code import CodeIndex, CodeProjects
from app.code.evidence import CodeSource, collect
from app.knowledge import KnowledgeBase
from app.main import app
from app.runtime.codetools import CodeAccess
from app.runtime.evidencetool import project_evidence_tool


class Vectors:
    def embed(self, texts: list[str]) -> np.ndarray:
        return np.array([[0.8, 0.6] if "linked marker" in text else [1, 0] for text in texts], dtype=np.float32)

    def embed_query(self, query: str) -> np.ndarray:
        return np.array([1, 0], dtype=np.float32)


def test_project_scope_applies_before_vector_and_keyword_cutoffs() -> None:
    kb = KnowledgeBase(":memory:", Vectors)  # type: ignore[arg-type]
    try:
        for i in range(24):
            kb.add(f"outside-{i}.txt", f"quota quota quota quota quota {i}".encode())
        allowed = kb.add("linked.txt", b"linked marker and quota")
        kb.set_project_documents("project", [allowed.id, allowed.id])
        assert kb.search("semantic challenge", k=1)[0].document.startswith("outside")
        assert [h.document for h in kb.search("semantic challenge", project_id="project")] == ["linked.txt"]
        # 关闭向量，只验关键词也会先过滤范围。
        kb._get_embedder = lambda: None
        assert kb.search("quota", k=1)[0].document.startswith("outside")
        assert [h.document for h in kb.search("quota", project_id="project")] == ["linked.txt"]
        assert kb.search("quota", project_id="other") == []
        with pytest.raises(sqlite3.IntegrityError):
            kb.set_project_documents("project", ["deleted"])
        assert kb.project_documents("project") == [allowed.id]
        kb.delete(allowed.id)
        assert kb.project_documents("project") == []
    finally:
        kb.close()


def test_requirement_symbol_leads_to_two_code_hops(tmp_path: Path) -> None:
    (tmp_path / "checkout.py").write_text(
        "def begin_order():\n    return normalize()\n\n"
        "def normalize():\n    return check_limit()\n\n"
        "def check_limit():\n    return 6 * 3600\n", encoding="utf-8",
    )
    # 索引只读项目内源码，也不收工作区里需要确认的密钥文件。
    (tmp_path / "credentials.py").write_text("def begin_order(): return 'secret'\n", encoding="utf-8")
    kb = KnowledgeBase(":memory:", lambda: None)
    doc = kb.add("rules.md", "## 刷新频率\n\n刷新频率从 \x60begin_order\x60 进入，每两小时检查。".encode())
    kb.set_project_documents("p", [doc.id])
    index = CodeIndex(tmp_path, ":memory:", lambda: None)
    try:
        sources = collect(index, kb, "p", "刷新频率")
        code = [s for s in sources if isinstance(s, CodeSource)]
        assert [(s.symbol, s.depth, s.relation, s.from_id) for s in code] == [
            ("begin_order", 0, "symbol", "d1"), ("normalize", 1, "callee", "c1"), ("check_limit", 2, "callee", "c2")
        ]
        assert (code[2].file, code[2].start, code[2].end, code[2].text) == (
            "checkout.py", 7, 8, "def check_limit():\n    return 6 * 3600"
        )
    finally:
        index.close()
        kb.close()


@pytest.mark.parametrize(("language", "caller", "callee", "relation", "key"), [
    ("ts", "function sendIt() { return ipcRenderer.invoke('code:graph') }\n",
     "ipcMain.handle('code:graph', () => 'map')\n", "ipc", "code:graph"),
    ("py", 'def send_it():\n    return requests.get("/hello")\n',
     '@router.get("/hello")\ndef greet():\n    return "hi"\n', "http", "/hello"),
])
def test_cross_process_links_have_the_endpoint_in_both_sources(
    tmp_path: Path, language: str, caller: str, callee: str, relation: str, key: str,
) -> None:
    (tmp_path / f"caller.{language}").write_text(caller, encoding="utf-8")
    (tmp_path / f"callee.{language}").write_text(callee, encoding="utf-8")
    index = CodeIndex(tmp_path, ":memory:", lambda: None)
    kb = KnowledgeBase(":memory:", lambda: None)
    try:
        sources = collect(index, kb, "p", "sendIt" if language == "ts" else "send_it")
        by_id = {s.id: s for s in sources}
        server = next(s for s in sources if isinstance(s, CodeSource) and s.relation == relation)
        assert server.file == f"callee.{language}" and server.via == key
        assert key in server.text and key in by_id[server.from_id].text
    finally:
        index.close()
        kb.close()


def test_project_evidence_endpoint_binds_claims_to_returned_excerpts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "gate.py").write_text("def check_quota():\n    return 6\n", encoding="utf-8")
    project = deps.get_code_projects().add(str(root))
    kb = deps.get_knowledge()
    visible = kb.add("requirements.md", b"## Quota\n\nCheck quota after 2 hours.")
    kb.add("other.md", b"Check quota after 99 hours.")
    queries = []

    class Model:
        async def stream(self, messages: list, tools=None):
            queries.append(json.loads(messages[-1]["content"]))
            refs = [s["id"] for s in queries[-1]["sources"] if "file" in s]
            yield json.dumps({"claims": [
                {"text": "The implementation returns 6.", "sources": refs[:1],
                 "kind": "supported", "quotes": {refs[0]: "return 6"}},
                {"text": "Guessed quote", "sources": refs[:1],
                 "kind": "supported", "quotes": {refs[0]: "return 99"}},
                {"text": "Mixed invalid citation", "sources": refs[:1] + ["made-up"]},
                {"text": "No citation", "sources": []},
            ], "gaps": ["Unit of the return value"]})

    monkeypatch.setitem(app.dependency_overrides, deps.get_model, Model)
    client = TestClient(app)
    url = f"/code/projects/{project.id}"
    assert client.get(f"{url}/documents").json()["linked"] == []
    res = client.put(f"{url}/documents", json={"linked": [visible.id]})
    assert res.status_code == 200 and res.json()["linked"] == [visible.id]
    assert client.put(f"{url}/documents", json={"linked": ["missing"]}).status_code == 422
    assert client.get(f"{url}/documents").json()["linked"] == [visible.id]
    body = client.post(f"{url}/evidence", json={"query": "Check quota"}).json()
    assert body["status"] == "answered" and len(body["claims"]) == 1
    assert body["claims"][0]["kind"] == "supported" and body["sent"]
    assert list(body["claims"][0]["quotes"].values()) == ["return 6"]
    inspection = client.post(f"{url}/evidence", json={"query": "Check quota", "mode": "inspect"}).json()
    assert inspection["status"] == "inspection" and not inspection["sent"]
    assert inspection["sources"] == body["sources"] and len(queries) == 1
    assert body["gaps"] == ["Unit of the return value"]
    assert [s["document"] for s in queries[-1]["sources"] if "document" in s] == ["requirements.md"]
    assert body["sources"][0]["heading"] == "Quota" and len(body["sources"][0]["version"]) == 12

    class Unavailable:
        async def stream(self, messages: list, tools=None):
            yield "unusable response"

    monkeypatch.setitem(app.dependency_overrides, deps.get_model, Unavailable)
    fallback = client.post(f"{url}/evidence", json={"query": "Check quota"}).json()
    assert fallback["status"] == "sources_only" and fallback["sources"] == body["sources"]
    assert fallback["claims"] == []


@pytest.mark.anyio
async def test_agent_reads_isolated_workspace_and_original_project_documents(tmp_path: Path) -> None:
    root = tmp_path / "original"
    root.mkdir()
    work = tmp_path / "worktree"
    work.mkdir()
    (root / "gate.py").write_text("def check_quota():\n    return 6\n", encoding="utf-8")
    (work / "gate.py").write_text("def check_quota():\n    return 2\n", encoding="utf-8")
    projects = CodeProjects(tmp_path / "projects", lambda: None)
    project = projects.add(str(root))
    kb = KnowledgeBase(":memory:", lambda: None)
    try:
        doc = kb.add("requirements.md", b"check_quota every 2 hours")
        kb.set_project_documents(project.id, [doc.id])
        tool = project_evidence_tool(CodeAccess(projects, root), work, lambda: kb)
        result = await tool.run({"query": "check_quota"})
        assert result.ok and "return 2" in result.content and "return 6" not in result.content
        assert result.extra["evidence"][0]["document"] == doc.name
        assert not projects.index(project.id).outline("gate.py")
    finally:
        kb.close()


def test_rerank_receives_scoped_candidates_and_keeps_exclusions(tmp_path: Path) -> None:
    from app.code.evidence import RetrievalTrace

    kb = KnowledgeBase(":memory:", lambda: None)
    index = CodeIndex(tmp_path, ":memory:", lambda: None)
    try:
        linked = [kb.add(f"policy{i}.txt", f"schedule update related policy {i}".encode()) for i in range(6)]
        kb.add("private.txt", b"schedule update unrelated private policy")
        kb.set_project_documents("p", [doc.id for doc in linked])

        def scorer(query: str, candidates: list[str]) -> list[float]:
            assert all("private" not in candidate for candidate in candidates)
            return [100 if "policy5" in candidate else 0 for candidate in candidates]

        trace = RetrievalTrace()
        sources = collect(index, kb, "p", "schedule update", scorer=scorer, trace=trace)
        assert sources[0].document == "policy5.txt"
        assert trace.reranked == ["documents"] and trace.document_candidates == 6
        assert len(trace.excluded) == 2
        assert not ({s.document for s in trace.excluded} & {s.document for s in sources})
    finally:
        index.close()
        kb.close()


def test_versioned_decisions_are_scoped_and_keep_original_message(tmp_path: Path) -> None:
    from app.runtime.evidencetool import decision_sources
    from app.runtime.memory import ProjectMemory
    from app.runtime.session import SessionStore

    root, other = tmp_path / "project", tmp_path / "other"
    root.mkdir()
    other.mkdir()
    sessions = SessionStore(tmp_path / "sessions")
    session = sessions.create(root, "ask", "mock", "")
    original = "安装包验收用当前 Windows 用户"
    session.append({"type": "user", "text": original})
    seq = session.events[-1]["seq"]
    memory = ProjectMemory(tmp_path / "notes.json")
    older, _ = memory.add(root, original, "agent", session.id, message_seq=seq)
    newer, _ = memory.add(root, "安装包验收暂停，等干净用户", replaces=older.id)
    memory.add(other, "安装包验收给所有人", "agent", session.id, message_seq=seq)

    assert [s.text for s in decision_sources(memory, sessions, root, "安装包验收")] == [newer.text]
    timeline = decision_sources(memory, sessions, root, "安装包验收", history=True)
    assert [s.text for s in timeline] == [newer.text, older.text]
    assert timeline[1].session == session.id and timeline[1].message == original
    assert timeline[1].until == newer.t and timeline[0].supersedes == older.id
    assert decision_sources(memory, sessions, root, "安装包验收", as_of=older.t)[0].text == older.text
    assert decision_sources(memory, sessions, other, "安装包验收")[0].message is None
    assert older.text not in memory.context(root)
    assert memory.remove(root, newer.id) and memory.history(root) == []


def test_route_wrapper_and_parameter_show_actual_endpoints(tmp_path: Path) -> None:
    (tmp_path / "bridge.ts").write_text(
        'function sendRequest(base: string, path = "") {\n'
        '    return fetch(\x60${base}/jobs${path}\x60)\n}\n'
        'export function runner() { return sendRequest(base, "/get/hello") }\n',
        encoding="utf-8",
    )
    (tmp_path / "jobs.py").write_text(
        'router = APIRouter(prefix="/jobs")\n\n'
        '@router.get("/get/{request_id}")\ndef details(request_id):\n'
        '    return request_id\n',
        encoding="utf-8",
    )
    index = CodeIndex(tmp_path, ":memory:", lambda: None)
    kb = KnowledgeBase(":memory:", lambda: None)
    try:
        sources = collect(index, kb, "p", "runner")
        endpoints = [c for c in sources if isinstance(c, CodeSource) and c.relation == "http"]
        assert endpoints and endpoints[0].file == "jobs.py"
        assert endpoints[0].start == 3 and '/get/{request_id}' in endpoints[0].text
        assert endpoints[0].via == "/jobs/get/{}"
    finally:
        index.close()
        kb.close()


def test_file_reference_finds_identifier_past_truncated_module(tmp_path: Path) -> None:
    lines = ["# This unrelated source line precedes the declaration"] * 100
    lines += ["POLL_INTERVAL = 6 * 3600", "def read_interval():", "    return POLL_INTERVAL"]
    (tmp_path / "timer.py").write_text("\n".join(lines), encoding="utf-8")
    (tmp_path / "test_timer.py").write_text(
        "from timer import POLL_INTERVAL\n\ndef test_timer():\n    assert POLL_INTERVAL > 0\n", encoding="utf-8",
    )
    index = CodeIndex(tmp_path, ":memory:", lambda: None)
    kb = KnowledgeBase(":memory:", lambda: None)
    try:
        index.refresh(embed=False)
        doc = kb.add("spec.md", "检查周期是 \x60POLL_INTERVAL\x60。".encode())
        kb.set_project_documents("p", [doc.id])
        sources = collect(index, kb, "p", "检查周期")
        assert any(isinstance(s, CodeSource) and "POLL_INTERVAL = 6 * 3600" in s.text for s in sources)
        assert any(isinstance(s, CodeSource) and s.relation == "test_reference" for s in sources)
    finally:
        index.close()
        kb.close()
