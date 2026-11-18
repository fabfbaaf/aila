"""星图里跨进程的光线：谁请求了哪个 HTTP 接口、谁发了哪个 IPC 消息，连到提供它、处理它的文件。

为什么单独扫：调用图按函数名连、只在同一类语言里连。桌面端和 AI 服务之间、桌面端和云端之间走 HTTP，
Electron 的渲染层（预加载）和主进程之间走 IPC，按函数名永远连不上，星图上就成了互不相干的几座岛。

只认代码里字面写出来的地址和频道名（模板字符串里的变量当成一段参数），拼出来的认不出就不连：宁可少连，不乱连。
- 提供接口：FastAPI 的 @router.get("/x")（加上 APIRouter 的 prefix）、Spring 的 @GetMapping（加上类上的
  @RequestMapping）、Express 的 app.get("/x")
- 请求接口：任何语言里像接口路径的字符串（"/agent/sessions"、`${base}/code/${id}`、"http://host:8765/health"）
- IPC：ipcMain.handle / ipcRenderer.on 收，ipcRenderer.invoke / webContents.send 发
"""

import re
from dataclasses import dataclass

PARAM = "{}"


@dataclass(frozen=True)
class Endpoint:
    kind: str  # http / ipc
    role: str  # serve：提供（处理）它；call：请求（发出）它
    key: str  # http：规整过的路径（参数写成 {}）；ipc：频道名


# ---------- HTTP：提供接口 ----------

PY_ROUTE = re.compile(r"@\w+\.(?:get|post|put|delete|patch|websocket|api_route|route)\(\s*[rfu]?([\"'])(.*?)\1")
PY_PREFIX = re.compile(r"APIRouter\([^)]*?prefix\s*=\s*([\"'])(.*?)\1")
JAVA_MAPPING = re.compile(
    r"@(Get|Post|Put|Delete|Patch|Request)Mapping\b(?:\(\s*(?:(?:value|path)\s*=\s*)?\{?\s*(\"([^\"]*)\")?)?"
)
JAVA_CLASS = re.compile(r"\b(?:class|interface|record)\s+\w+")
JS_ROUTE = re.compile(r"\b(?:app|router|server)\.(?:get|post|put|delete|patch)\(\s*(['\"`])(/[^'\"`]*)\1")

# ---------- HTTP：请求接口 ----------

STRING = {
    "python": re.compile(r"(?<![\w])[rbfuRBFU]{0,2}(\"|')((?:\\.|(?!\1).)*?)\1"),
    "web": re.compile(r"(['\"`])((?:\\.|(?!\1)[^\n])*?)\1"),
    "java": re.compile(r"\"((?:\\.|[^\"\\\n])*)\""),
}
URL_HEAD = re.compile(r"^(?:https?|wss?)://[^/\s]+")
INTERP = re.compile(r"\$\{[^}]*\}|\{[^}]*\}")
PATH_LIKE = re.compile(r"^/[A-Za-z_]")

# ---------- IPC ----------

IPC_RECEIVE = re.compile(r"\bipc(?:Main|Renderer)\.(?:handle|handleOnce|on|once)\(\s*(['\"`])([\w:./-]+)\1")
IPC_SEND = re.compile(r"(?:\bipcRenderer\.(?:invoke|send|sendSync)|\.send)\(\s*(['\"`])([\w:./-]+)\1")

FAMILY = {"python": "python", "java": "java", "javascript": "web", "typescript": "web", "tsx": "web", "vue": "web"}


def normalize(path: str) -> str | None:
    """一个字符串 → 规整过的接口路径（参数写成 {}）；不像接口路径返回 None

    "http://127.0.0.1:8765/agent/x?y=1" → /agent/x；"{}/code/{}/graph"（${base}/code/${id}/graph）→ /code/{}/graph；
    结尾的 / 表示后面还拼了一段（'/api/settings/' + key）→ /api/settings/{}
    """
    s = INTERP.sub(PARAM, path.strip())
    s = URL_HEAD.sub("", s)
    while s.startswith(PARAM):
        s = s[len(PARAM):]  # 开头是变量（服务地址）
    s = re.split(r"[?#]", s, maxsplit=1)[0]
    if not PATH_LIKE.match(s) or any(c.isspace() for c in s) or "//" in s:
        return None
    parts = s.split("/")[1:]
    if parts and parts[-1] == "":
        parts[-1] = PARAM
    if "." in parts[-1] and PARAM not in parts[-1]:
        return None  # /index.html、/logo.png：是文件，不是接口
    return "/" + "/".join(PARAM if PARAM in p else p for p in parts)


def route_key(path: str) -> str:
    """提供接口一侧的路径：{sid}、{p:path}、:id 都写成 {}；{p:path} 记成 {…}，后面几段都能对上"""
    parts = [p for p in path.strip().split("/") if p]
    out = []
    for p in parts:
        if re.fullmatch(r"\{[^}]*:path\}", p):
            out.append("{…}")
        elif p.startswith(":") or "{" in p:
            out.append(PARAM)
        else:
            out.append(p)
    return "/" + "/".join(out)


def _join(prefix: str, path: str) -> str:
    return "/" + "/".join(p for p in f"{prefix}/{path}".split("/") if p)


def scan(path: str, text: str, lang: str) -> list[Endpoint]:
    """一个文件提供了哪些接口、请求了哪些接口、收发哪些 IPC 消息"""
    family = FAMILY.get(lang, "")
    found: set[Endpoint] = set()
    serving: set[str] = set()  # 提供接口那几行里的路径：同一个字符串不再算成请求
    test = is_test(path)  # 测试文件不算提供接口（里面写的路由多半是测试数据），只算请求

    if family == "python":
        m = PY_PREFIX.search(text)
        prefix = m.group(2) if m else ""
        for m in PY_ROUTE.finditer(text):
            serving.add(m.group(2))
            if not test:
                found.add(Endpoint("http", "serve", route_key(_join(prefix, m.group(2)))))
    elif family == "java":
        cls = JAVA_CLASS.search(text)
        top = cls.start() if cls else 0
        prefix = ""
        for m in JAVA_MAPPING.finditer(text):
            value = m.group(3) or ""
            serving.add(value)
            if m.group(1) == "Request" and m.start() < top:
                prefix = value  # 类上的 @RequestMapping：下面每个方法的前缀
                continue
            if not test:
                found.add(Endpoint("http", "serve", route_key(_join(prefix, value))))
    elif family == "web":
        for m in JS_ROUTE.finditer(text):
            serving.add(m.group(2))
            if not test:
                found.add(Endpoint("http", "serve", route_key(m.group(2))))
        for m in IPC_RECEIVE.finditer(text):
            if not test:
                found.add(Endpoint("ipc", "serve", m.group(2)))
        for m in IPC_SEND.finditer(text):
            found.add(Endpoint("ipc", "call", m.group(2)))

    pattern = STRING.get(family)
    if pattern:
        for m in pattern.finditer(text):
            raw = m.group(m.lastindex or 1)
            if raw in serving:
                continue
            key = normalize(raw)
            if key:
                found.add(Endpoint("http", "call", key))
    return sorted(found, key=lambda e: (e.kind, e.role, e.key))


def _score(call: list[str], route: list[str]) -> float | None:
    """请求的路径和提供的路径对不对得上；对得上返回分数（字面一样的段越多越高），对不上 None"""
    rest = route and route[-1] == "{…}"
    if rest:
        route = route[:-1]
        if len(call) <= len(route):
            return None
        call = call[: len(route)]
    if len(call) != len(route):
        return None
    score, literal = 0.0, 0
    for c, r in zip(call, route, strict=True):
        if c == PARAM and r == PARAM:
            score += 0.5
        elif r == PARAM:
            continue  # 接口这一段是参数：请求里写死的值也对得上
        elif c == PARAM:
            return None  # 请求里是变量、接口这一段写死：多半是别的东西（页面地址 /settings/${tab}），不连
        elif c == r:
            score += 1
            literal += 1
        else:
            return None
    return score if literal else None  # 一段字面都没对上（/{}）不算


def link(found: dict[str, list[Endpoint]]) -> dict[tuple[str, str, str], set[str]]:
    """(请求的文件, 提供的文件, http/ipc) → 经过的接口路径或频道名。同一个文件里的不连"""
    served: dict[tuple[str, str], set[str]] = {}
    for file, eps in found.items():
        for e in eps:
            if e.role == "serve":
                served.setdefault((e.kind, e.key), set()).add(file)
    routes = [(key, key.split("/")[1:], files) for (kind, key), files in served.items() if kind == "http"]
    out: dict[tuple[str, str, str], set[str]] = {}
    for file, eps in found.items():
        for e in eps:
            if e.role != "call":
                continue
            if e.kind == "ipc":
                targets = [(e.key, served.get(("ipc", e.key), set()))]
            else:
                parts = e.key.split("/")[1:]
                scored = [(s, key, files) for key, segs, files in routes if (s := _score(parts, segs)) is not None]
                best = max((s for s, _, _ in scored), default=None)
                targets = [(key, files) for s, key, files in scored if s == best]
            for key, files in targets:
                for dst in files:
                    if dst != file:
                        out.setdefault((file, dst, e.kind), set()).add(key)
    return out


TEST_DIRS = {"test", "tests", "__tests__", "spec", "testing"}
TEST_NAME = re.compile(
    r"^(test_.*\.py|.*_test\.py|conftest\.py|.*\.(test|spec)\.[cm]?[jt]sx?|.*Tests?\.java|.*IT\.java)$"
)


def is_test(path: str) -> bool:
    """测试文件：在 tests/ 这类目录里，或者名字像 test_x.py、x.test.ts、XTest.java"""
    parts = path.split("/")
    return bool(TEST_NAME.match(parts[-1])) or any(p in TEST_DIRS for p in parts[:-1])
