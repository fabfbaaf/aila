"""项目里「忽略这些文件夹」：.ailaignore 和「项目」页里加的合起来用；代码检索、星图都不看忽略掉的文件"""

from pathlib import Path

from fastapi.testclient import TestClient

from app import deps
from app.code.ignore import clean, ignored
from app.code.projects import CodeProjects
from app.main import app


def test_patterns() -> None:
    assert clean(["# 注释", "", " docs/ ", "docs", r"a\b", "../x", "./build"]) == ["docs", "a/b", "build"]
    assert ignored("aila-ai/eval/tasks/fix/repo/cart.py", ["aila-ai/eval/tasks"])
    assert not ignored("aila-ai/eval/run.py", ["aila-ai/eval/tasks"])
    assert ignored("web/fixtures/x.ts", ["fixtures"]) and not ignored("web/fixturesX/x.ts", ["fixtures"])
    assert ignored("static/app.min.js", ["*.min.js"]) and ignored("src/generated/deep/a.ts", ["src/generated/*"])
    assert not ignored("src/app.js", ["*.min.js"])


def make(root: Path) -> None:
    for rel in ("app/main.py", "eval/tasks/demo/repo/cart.py", "vendor_like/x.py", "tests/test_main.py"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("def f():\n    return 1\n", encoding="utf-8")


def test_ignore_file_and_project_setting(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    make(root)
    (root / ".ailaignore").write_text("# 评测用的示例项目\neval/tasks\n", encoding="utf-8")
    projects = CodeProjects(tmp_path / "data", lambda: None)
    p = projects.add(str(root))
    idx = projects.index(p.id)
    idx.refresh(embed=False)
    files = {n["id"] for n in idx.graph()["nodes"]}
    assert files == {"app/main.py", "vendor_like/x.py", "tests/test_main.py"}
    projects.set_ignore(p.id, ["vendor_like", "", "# x"])
    assert projects.get(p.id).ignore == ["vendor_like"]  # type: ignore[union-attr]
    idx.refresh(embed=False)  # 已经在索引里的也清出去
    assert {n["id"] for n in idx.graph()["nodes"]} == {"app/main.py", "tests/test_main.py"}
    described = projects.describe()[0]
    assert described["ignore"] == ["vendor_like"] and described["ignoreFile"] == ["eval/tasks"]


def test_route(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    make(root)
    projects = CodeProjects(tmp_path / "data", lambda: None)
    p = projects.add(str(root))
    app.dependency_overrides[deps.get_code_projects] = lambda: projects
    try:
        client = TestClient(app)
        r = client.put(f"/code/projects/{p.id}/ignore", json={"patterns": ["eval/tasks", "../etc"]})
        assert r.status_code == 200 and r.json()["ignore"] == ["eval/tasks"]
        assert client.put("/code/projects/nope/ignore", json={"patterns": []}).status_code == 404
        assert client.put(f"/code/projects/{p.id}/ignore", json={"patterns": ["x"] * 101}).status_code == 422
    finally:
        app.dependency_overrides.clear()
