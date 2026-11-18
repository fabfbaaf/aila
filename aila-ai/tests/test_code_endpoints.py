"""星图里跨进程的光线：HTTP 接口、IPC 消息连到提供它的文件；模块、测试文件标记"""

from pathlib import Path

from app.code.endpoints import Endpoint, is_test, link, normalize, route_key, scan
from app.code.index import CodeIndex

ROUTES_PY = '''from fastapi import APIRouter

router = APIRouter(prefix="/agent")


@router.get("/sessions/{sid}")
async def one(sid: str): ...


@router.post("/sessions/{sid}/stop")
async def stop(sid: str): ...


@router.websocket("/stream")
async def stream(ws): ...
'''

FILES_PY = '''from fastapi import APIRouter

router = APIRouter()


@router.get("/files/{path:path}")
async def get_file(path: str): ...
'''

CONTROLLER_JAVA = '''package com.shop.sync;

@RestController
@RequestMapping("/api")
public class SyncController {
    @GetMapping("/sync")
    public Object sync() { return null; }

    @PutMapping("/settings/{key}")
    public Object put(@PathVariable String key) { return null; }
}
'''

CLIENT_TS = '''const base = 'http://127.0.0.1:8765'
export async function load(id: string) {
  await fetch(`${base}/agent/sessions/${id}`)
  await fetch(`${base}/agent/sessions/${id}/stop`, { method: 'POST' })
  new WebSocket('ws://127.0.0.1:8765/agent/stream')
  await fetch(`/api/settings/${encodeURIComponent(id)}`)
  await fetch('/api/sync?since=3')
  await fetch(`/files/${id}/raw/all`)
  location.hash = `/settings/${id}`  // 页面地址，不是接口
  const logo = '/static/logo.png'
}
'''

PRELOAD_TS = '''import { ipcRenderer } from 'electron'
export const api = {
  graph: (id: string) => ipcRenderer.invoke('code:graph', id),
  hide: () => ipcRenderer.send('win:hide'),
  missing: () => ipcRenderer.invoke('nobody:handles')
}
'''

MAIN_TS = '''import { ipcMain } from 'electron'
ipcMain.handle('code:graph', async (_e, id) => id)
ipcMain.on('win:hide', () => {})
'''


def test_normalize_call_paths() -> None:
    assert normalize("http://127.0.0.1:8765/agent/x?y=1") == "/agent/x"
    assert normalize("${base}/code/${id}/graph".replace("${", "{")) == "/code/{}/graph"
    assert normalize("/api/settings/") == "/api/settings/{}"
    assert normalize("ws://host/voice") == "/voice"
    for not_api in ("/index.html", "hello world", "/", "#/tasks", "//cdn", "./a/b", "C:/Users"):
        assert normalize(not_api) is None, not_api


def test_route_keys() -> None:
    assert route_key("/agent/sessions/{sid}") == "/agent/sessions/{}"
    assert route_key("/users/:id") == "/users/{}"
    assert route_key("/files/{p:path}") == "/files/{…}"


def test_scan_finds_served_and_called_endpoints() -> None:
    served = {e.key for e in scan("app/routes/agent.py", ROUTES_PY, "python") if e.role == "serve"}
    assert served == {"/agent/sessions/{}", "/agent/sessions/{}/stop", "/agent/stream"}
    java = {e.key for e in scan("server/SyncController.java", CONTROLLER_JAVA, "java") if e.role == "serve"}
    assert java == {"/api/sync", "/api/settings/{}"}  # 类上的 @RequestMapping 是前缀，自己不算接口
    calls = {e.key for e in scan("web/client.ts", CLIENT_TS, "typescript") if e.role == "call"}
    assert {"/agent/sessions/{}", "/agent/sessions/{}/stop", "/agent/stream", "/api/settings/{}", "/api/sync"} <= calls
    # 测试文件里写的路由是测试数据：不算它提供接口，只算请求
    assert not [e for e in scan("tests/test_routes.py", ROUTES_PY, "python") if e.role == "serve"]
    ipc = scan("preload/index.ts", PRELOAD_TS, "typescript")
    assert Endpoint("ipc", "call", "code:graph") in ipc and Endpoint("ipc", "call", "win:hide") in ipc


def test_link_connects_callers_to_providers() -> None:
    found = {
        "app/routes/agent.py": scan("app/routes/agent.py", ROUTES_PY, "python"),
        "app/routes/files.py": scan("app/routes/files.py", FILES_PY, "python"),
        "server/SyncController.java": scan("server/SyncController.java", CONTROLLER_JAVA, "java"),
        "web/client.ts": scan("web/client.ts", CLIENT_TS, "typescript"),
        "preload/index.ts": scan("preload/index.ts", PRELOAD_TS, "typescript"),
        "main/index.ts": scan("main/index.ts", MAIN_TS, "typescript"),
    }
    links = link(found)
    assert links[("web/client.ts", "app/routes/agent.py", "http")] == {
        "/agent/sessions/{}", "/agent/sessions/{}/stop", "/agent/stream"}
    assert links[("web/client.ts", "server/SyncController.java", "http")] == {"/api/settings/{}", "/api/sync"}
    assert links[("web/client.ts", "app/routes/files.py", "http")] == {"/files/{…}"}  # {path:path} 接住后面几段
    assert links[("preload/index.ts", "main/index.ts", "ipc")] == {"code:graph", "win:hide"}
    # 页面地址 /settings/${id} 没有连到任何接口；提供接口的文件不会连到自己
    assert all(s != t for s, t, _ in links) and len(links) == 4


def test_is_test() -> None:
    for path in ("aila-ai/tests/test_x.py", "src/agent/fold.test.ts", "server/src/test/java/AppTest.java",
                 "a/conftest.py", "web/__tests__/x.ts", "src/x.spec.tsx"):
        assert is_test(path), path
    for path in ("aila-ai/app/runtime/loop.py", "src/contest.ts", "src/latest/x.ts"):
        assert not is_test(path), path


class NoEmbedder:
    def embed(self, texts):  # pragma: no cover - 用不到
        raise AssertionError


def test_graph_has_modules_tests_and_cross_links(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    files = {
        "ai/app/routes/agent.py": ROUTES_PY,
        "ai/tests/test_agent.py": 'def test_one(client):\n    client.get("/agent/sessions/1")\n',
        "desktop/src/main/client.ts": CLIENT_TS,
        "README.py": "x = 1\n",
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    idx = CodeIndex(root, tmp_path / "code.sqlite3", lambda: None)
    idx.refresh(embed=False)
    g = idx.graph()
    nodes = {n["id"]: n for n in g["nodes"]}
    assert nodes["ai/app/routes/agent.py"]["module"] == "ai/app/routes" and nodes["README.py"]["module"] == "."
    assert nodes["ai/tests/test_agent.py"]["test"] and not nodes["ai/app/routes/agent.py"]["test"]
    http = {(lk["source"], lk["target"]): lk for lk in g["links"] if lk["kind"] == "http"}
    assert set(http) == {("desktop/src/main/client.ts", "ai/app/routes/agent.py"),
                         ("ai/tests/test_agent.py", "ai/app/routes/agent.py")}
    cross = http[("desktop/src/main/client.ts", "ai/app/routes/agent.py")]
    assert cross["weight"] == 3 and "/agent/stream" in cross["via"]  # type: ignore[operator]

    # 文件改了：重新扫；没改：用缓存，不再读
    (root / "desktop/src/main/client.ts").write_text("export const x = 1\n", encoding="utf-8")
    idx.refresh(embed=False)
    assert not [lk for lk in idx.graph()["links"] if lk["source"] == "desktop/src/main/client.ts"]
    idx.close()
