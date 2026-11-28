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
from bisect import bisect_right
from dataclasses import dataclass, field, replace

PARAM = "{}"


@dataclass(frozen=True)
class Endpoint:
    kind: str  # http / ipc
    role: str  # serve：提供（处理）它；call：请求（发出）它
    key: str  # http：规整过的路径（参数写成 {}）；ipc：频道名
    lines: tuple[int, ...] = field(default=(), compare=False)


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


# 本地 HTTP 封装常把固定前缀和第二个参数拼起来，例如 fetch(`\${url}/discoveries\${path}`)。
JS_FETCH_PREFIX = re.compile(
    r"\bfunction\s+(\w+)(?:<[^>]+>)?\s*\(\s*\w+\s*:[^,]{1,100},\s*(\w+)\s*[:=][^)]{0,150}\)"
    r"[^;]{0,350}?\bfetch\(\s*`\$\{\w+\}(/[^$`]+)\$\{\2\}`"
)

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


# 注释里的 // 和 /* */：// 前面是冒号（http://）、引号、反斜杠的不算（在字符串里）
WEB_BLOCK = re.compile(r"/\*.*?\*/", re.S)
WEB_LINE = re.compile(r"(?<![:\"'`\\])//[^\n]*")


def without_notes(text: str, family: str) -> str:
    """把注释和文档字符串换成空格（换行留着，行号不变）：找「请求了哪个接口」时不算它们。
    不然文档里举的例子（「请求接口："/agent/sessions"」）会被当成真的请求，连出不存在的线"""
    blank = lambda m: re.sub(r"[^\n]", " ", m.group(0))  # noqa: E731
    if family == "python":
        return _python_without_notes(text)
    if family in ("web", "java"):
        return WEB_LINE.sub(blank, WEB_BLOCK.sub(blank, text))
    return text


def _python_without_notes(text: str) -> str:
    """Python：用标准库的分词认出注释和文档字符串（模块、类、函数开头单独一行的字符串），换成空格。
    分词失败（代码写到一半）就原样返回，宁可多连也别漏"""
    import io
    import tokenize

    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return text
    lines = text.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))

    def offset(pos: tuple[int, int]) -> int:
        row, col = pos
        return starts[row - 1] + col if row - 1 < len(starts) else len(text)

    spans: list[tuple[int, int]] = []
    before = tokenize.NEWLINE  # 上一个有意义的记号（跳过空行、注释）
    statement_start = {tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT}
    for i, tok in enumerate(tokens):
        if tok.type == tokenize.COMMENT:
            spans.append((offset(tok.start), offset(tok.end)))
        elif tok.type == tokenize.STRING and before in statement_start:
            after = next((t.type for t in tokens[i + 1:] if t.type not in (tokenize.NL, tokenize.COMMENT)), tokenize.ENDMARKER)
            if after in (tokenize.NEWLINE, tokenize.ENDMARKER):
                spans.append((offset(tok.start), offset(tok.end)))  # 单独一行的字符串：文档字符串
        if tok.type not in (tokenize.NL, tokenize.COMMENT):
            before = tok.type
    if not spans:
        return text
    out = list(text)
    for a, b in spans:
        for j in range(a, min(b, len(out))):
            if out[j] != "\n":
                out[j] = " "
    return "".join(out)


def scan(path: str, text: str, lang: str) -> list[Endpoint]:
    """一个文件提供了哪些接口、请求了哪些接口、收发哪些 IPC 消息"""
    family = FAMILY.get(lang, "")
    found: dict[Endpoint, list[int]] = {}
    line_starts = [m.start() + 1 for m in re.finditer("\n", text)]

    def add(endpoint: Endpoint, offset: int) -> None:
        found.setdefault(endpoint, []).append(bisect_right(line_starts, offset) + 1)

    serving: set[str] = set()  # 提供接口那几行里的路径：同一个字符串不再算成请求
    test = is_test(path)  # 测试文件不算提供接口（里面写的路由多半是测试数据），只算请求

    if family == "python":
        m = PY_PREFIX.search(text)
        prefix = m.group(2) if m else ""
        for m in PY_ROUTE.finditer(text):
            serving.add(m.group(2))
            if not test:
                add(Endpoint("http", "serve", route_key(_join(prefix, m.group(2)))), m.start(2))
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
                add(Endpoint("http", "serve", route_key(_join(prefix, value))), m.start())
    elif family == "web":
        for m in JS_ROUTE.finditer(text):
            serving.add(m.group(2))
            if not test:
                add(Endpoint("http", "serve", route_key(m.group(2))), m.start(2))
        for m in JS_FETCH_PREFIX.finditer(text):
            name, _, prefix = m.groups()
            calls = re.compile(rf"\b{re.escape(name)}(?:<[^<>\n]+>)?\(\s*\w+\s*,\s*(['\"])(/[^'\"]*)\1")
            for call in calls.finditer(text):
                key = normalize(_join(prefix, call.group(2)))
                if key:
                    add(Endpoint("http", "call", key), call.start(2))
        for m in IPC_RECEIVE.finditer(text):
            if not test:
                add(Endpoint("ipc", "serve", m.group(2)), m.start(2))
        for m in IPC_SEND.finditer(text):
            add(Endpoint("ipc", "call", m.group(2)), m.start(2))

    pattern = STRING.get(family)
    if pattern:
        for m in pattern.finditer(without_notes(text, family)):
            raw = m.group(m.lastindex or 1)
            if raw in serving:
                continue
            key = normalize(raw)
            if key:
                add(Endpoint("http", "call", key), m.start())
    return [replace(e, lines=tuple(sorted(set(found[e])))) for e in sorted(found, key=lambda e: (e.kind, e.role, e.key))]


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


def connections(found: dict[str, list[Endpoint]]) -> list[tuple[str, Endpoint, str, Endpoint]]:
    """保留请求和提供方的各自位置：两端路径参数的写法可能不同。"""
    served: dict[tuple[str, str], list[tuple[str, Endpoint]]] = {}
    for file, endpoints in found.items():
        for endpoint in endpoints:
            if endpoint.role == "serve":
                served.setdefault((endpoint.kind, endpoint.key), []).append((file, endpoint))
    routes = [(key.split("/")[1:], refs) for (kind, key), refs in served.items() if kind == "http"]
    out = []
    for file, endpoints in found.items():
        for endpoint in endpoints:
            if endpoint.role != "call":
                continue
            if endpoint.kind == "ipc":
                targets = served.get(("ipc", endpoint.key), [])
            else:
                parts = endpoint.key.split("/")[1:]
                scored = [(score, refs) for segs, refs in routes if (score := _score(parts, segs)) is not None]
                best = max((score for score, _ in scored), default=None)
                targets = [ref for score, refs in scored if score == best for ref in refs]
            out.extend((file, endpoint, dst, receiver) for dst, receiver in targets if dst != file)
    return out


def link(found: dict[str, list[Endpoint]]) -> dict[tuple[str, str, str], set[str]]:
    """星图只需要每对文件之间经过的接口路径或频道名。"""
    out: dict[tuple[str, str, str], set[str]] = {}
    for src, call, dst, receiver in connections(found):
        out.setdefault((src, dst, call.kind), set()).add(receiver.key)
    return out


TEST_DIRS = {"test", "tests", "__tests__", "spec", "testing"}
TEST_NAME = re.compile(
    r"^(test_.*\.py|.*_test\.py|conftest\.py|.*\.(test|spec)\.[cm]?[jt]sx?|.*Tests?\.java|.*IT\.java)$"
)


def is_test(path: str) -> bool:
    """测试文件：在 tests/ 这类目录里，或者名字像 test_x.py、x.test.ts、XTest.java"""
    parts = path.split("/")
    return bool(TEST_NAME.match(parts[-1])) or any(p in TEST_DIRS for p in parts[:-1])
