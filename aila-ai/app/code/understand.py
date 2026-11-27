"""看懂项目：把代码索引的图（CodeIndex.graph）整理成一层层能往下钻的视图。

整体架构（项目根目录下的几块）→ 模块（某个目录下的子目录和文件）→ 文件接口链路（从一个文件、或者一个 HTTP 接口
出发，顺着调用、HTTP 请求、IPC 消息一路连下去）→ 函数（谁调用它、它调用谁）。

为什么这样设计：
- 图上的方块和连线全部由代码从项目里算出来（index.graph 已经认出了函数调用、import、HTTP 接口、IPC 消息），
  AI 只在 explain.py 里根据这些事实讲解，不让它自己画结构：图一定是真的
- 「往下钻」只有一个函数 area(path)：根目录是整体架构，点开一块就是这一块下面的子目录和文件，层级多深都一样
- 只有一个子目录、没有文件的目录一路合并（Java 的 src/main/java/com/xxx 这种），不然要点好几下才看到东西
- 测试文件不参与连线（测试什么都调用，连上全是噪音），但还列出来、标成测试
纯函数（输入是 graph 的字典），方便单测。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

Graph = dict[str, list[dict[str, Any]]]

# 链路里优先顺着哪种连线走：跨进程的接口最能说明「这一步交给了谁」，import 只在没有别的连线时才用
KIND_ORDER = {"http": 0, "ipc": 1, "call": 2, "import": 3}
KIND_TEXT = {"call": "调用", "import": "引用", "http": "HTTP 接口", "ipc": "IPC 消息"}
# 入口文件的名字（整体架构里标出来：从哪里开始读）
ENTRY_NAMES = {"main", "__main__", "index", "app", "application", "server", "cli", "manage", "run", "start"}
MAX_CHILDREN = 60
MAX_CHAIN_NODES = 24


@dataclass
class Child:
    id: str  # 相对项目根目录的路径
    name: str
    kind: str  # dir / file
    files: int = 0
    lines: int = 0
    functions: int = 0
    tests: int = 0
    langs: dict[str, int] = field(default_factory=dict)

    def add(self, node: dict[str, Any]) -> None:
        self.files += 1
        self.lines += int(node.get("lines") or 0)
        self.functions += int(node.get("functions") or 0)
        self.tests += 1 if node.get("test") else 0
        lang = str(node.get("lang") or "")
        if lang:
            self.langs[lang] = self.langs.get(lang, 0) + 1

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "kind": self.kind, "files": self.files, "lines": self.lines,
                "functions": self.functions, "tests": self.tests, "onlyTests": self.files > 0 and self.tests == self.files,
                "langs": dict(sorted(self.langs.items(), key=lambda kv: -kv[1])[:4])}


ELECTRON_PROCESSES = ("main", "preload", "renderer")


def clean(graph: Graph) -> Graph:
    """去掉不可能的连线：函数调用、import 只发生在同一个程序里。
    索引按名字连调用，会把两个顶层目录（不同的程序，比如 AI 服务里手机页的 JS 和桌面端的 TS）、
    或者 Electron 的不同进程（main、preload、renderer）里同名的函数连起来；它们之间只能走 HTTP、IPC。
    shared 这种几个进程都编进去的目录不算进程"""
    nodes = graph.get("nodes", [])
    ids = [str(n["id"]) for n in nodes]
    # Electron 项目：同一个目录下面既有 main/ 又有 renderer/ 的，就是三个进程的分界
    electron: set[str] = set()
    for f in ids:
        parts = f.split("/")
        for i, part in enumerate(parts[:-1]):
            if part == "renderer":
                base = "/".join(parts[:i])
                if any(x.startswith(f"{base}/main/" if base else "main/") for x in ids):
                    electron.add(base)

    def top(f: str) -> str:
        return f.split("/", 1)[0] if "/" in f else ""

    def process(f: str) -> str:
        """Electron 的哪个进程；不在 main、preload、renderer 下面（shared、脚本）是空的"""
        for base in electron:
            prefix = f"{base}/" if base else ""
            if f.startswith(prefix):
                head = f[len(prefix):].split("/", 1)[0]
                if head in ELECTRON_PROCESSES:
                    return f"{base}:{head}"
        return ""

    kept = []
    for link in graph.get("links", []):
        if link["kind"] in ("call", "import"):
            s, t = str(link["source"]), str(link["target"])
            if top(s) != top(t):
                continue  # 两个不同的程序
            a, b = process(s), process(t)
            if a and b and a != b:
                continue  # Electron 的两个不同进程
        kept.append(link)
    return {**graph, "links": kept}


def _norm(path: str) -> str:
    return path.strip().replace("\\", "/").strip("/")


def _under(file: str, prefix: str) -> bool:
    return not prefix or file == prefix or file.startswith(prefix + "/")


def _collapse(prefix: str, files: list[str]) -> str:
    """只有一个子目录、没有直接放文件的目录一路往下合并：返回真正开始分叉的那一层"""
    while True:
        rest = [f[len(prefix) + 1:] if prefix else f for f in files if _under(f, prefix)]
        if not rest or any("/" not in r for r in rest):
            return prefix
        heads = {r.split("/", 1)[0] for r in rest}
        if len(heads) != 1:
            return prefix
        prefix = f"{prefix}/{heads.pop()}" if prefix else heads.pop()


def area(graph: Graph, path: str = "", tests: bool = False) -> dict[str, Any]:
    """一个目录下面这一层：子目录和文件是方块（带行数、函数数、语言），它们之间的连线按种类汇总；
    和这个目录外面的连线汇总到「外面的那一块」（同一个上级目录下的兄弟，或者另一个顶层目录）。
    path 为空就是整体架构"""
    nodes = graph.get("nodes", [])
    by_id = {str(n["id"]): n for n in nodes}
    files = list(by_id)
    path = _collapse(_norm(path), files)
    inside = [f for f in files if _under(f, path) and f != path]
    if not inside and path in by_id:  # 指到的是一个文件：它所在的目录
        path = path.rsplit("/", 1)[0] if "/" in path else ""
        inside = [f for f in files if _under(f, path)]

    children: dict[str, Child] = {}
    owner: dict[str, str] = {}
    for f in inside:
        rest = f[len(path) + 1:] if path else f
        head = rest.split("/", 1)[0]
        cid = f"{path}/{head}" if path else head
        is_dir = "/" in rest
        if is_dir:
            cid = _collapse(cid, inside)
        if cid not in children:
            children[cid] = Child(cid, cid[len(path) + 1:] if path else cid, "dir" if is_dir else "file")
        children[cid].add(by_id[f])
        owner[f] = cid

    def outside_of(f: str) -> str:
        """外面的一端算到哪一块：和 path 同一个顶层目录的，算到 path 的兄弟那一层；别的算到它的顶层目录"""
        top = f.split("/", 1)[0]
        if path and top == path.split("/", 1)[0]:
            parent = path.rsplit("/", 1)[0] if "/" in path else ""
            rest = f[len(parent) + 1:] if parent else f
            head = rest.split("/", 1)[0]
            return f"{parent}/{head}" if parent else head
        return top if "/" in f else "（根目录）"

    links: dict[tuple[str, str], dict[str, Any]] = {}
    outside: dict[str, dict[str, Any]] = {}
    for link in graph.get("links", []):
        s, t, kind = str(link["source"]), str(link["target"]), str(link["kind"])
        if s not in by_id or t not in by_id:
            continue
        if not tests and (by_id[s].get("test") or by_id[t].get("test")):
            continue
        a = owner.get(s) or outside_of(s)
        b = owner.get(t) or outside_of(t)
        if a == b or (s not in owner and t not in owner):
            continue
        for end in (a, b):
            if end not in children and end not in outside:
                outside[end] = {"id": end, "name": end, "kind": "outside"}
        entry = links.setdefault((a, b), {"source": a, "target": b, "kinds": {}, "via": set()})
        entry["kinds"][kind] = entry["kinds"].get(kind, 0) + int(link.get("weight") or 1)
        entry["via"].update(link.get("via") or [])

    ranked = sorted(children.values(), key=lambda c: (c.kind != "dir", -c.lines))[:MAX_CHILDREN]
    kept = {c.id for c in ranked} | set(outside)
    return {
        "path": path,
        "name": path.rsplit("/", 1)[-1] if path else "整个项目",
        "parent": (path.rsplit("/", 1)[0] if "/" in path else "") if path else None,
        "children": [c.as_dict() for c in ranked],
        "outside": sorted(outside.values(), key=lambda o: o["id"]),
        "links": [{**v, "via": sorted(v["via"])[:6]} for k, v in sorted(links.items()) if k[0] in kept and k[1] in kept],
        "entries": entries(graph, path),
        "hubs": hubs(graph, path),
        "hidden": max(0, len(children) - len(ranked)),
    }


def entries(graph: Graph, path: str = "", limit: int = 3) -> list[str]:
    """从哪里开始读：名字像入口的（main、index、app……）、或者只往外连没人连进来的文件，按大小排"""
    nodes = [n for n in graph.get("nodes", []) if _under(str(n["id"]), path) and not n.get("test")]
    incoming = {str(link["target"]) for link in graph.get("links", []) if link["kind"] != "import"}
    outgoing = {str(link["source"]) for link in graph.get("links", [])}
    named = [n for n in nodes if str(n["name"]).rsplit(".", 1)[0].lower() in ENTRY_NAMES]
    roots = [n for n in nodes if str(n["id"]) in outgoing and str(n["id"]) not in incoming]
    picked: list[str] = []
    for n in sorted(named, key=lambda n: (str(n["id"]).count("/"), -int(n.get("lines") or 0))) + \
            sorted(roots, key=lambda n: -int(n.get("lines") or 0)):
        if str(n["id"]) not in picked:
            picked.append(str(n["id"]))
    return picked[:limit]


def hubs(graph: Graph, path: str = "", limit: int = 3) -> list[str]:
    """被最多文件用到的（改它影响面最大，读懂它最划算）"""
    test = {str(n["id"]) for n in graph.get("nodes", []) if n.get("test")}
    users: dict[str, set[str]] = defaultdict(set)
    for link in graph.get("links", []):
        s, t = str(link["source"]), str(link["target"])
        if s in test or t in test or not _under(t, path):
            continue
        users[t].add(s)
    return [f for f, _ in sorted(users.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:limit]]


def chain(graph: Graph, start: str = "", endpoint: str = "", down: int = 4, up: int = 2, width: int = 3) -> dict[str, Any]:
    """文件接口链路：从一个文件（或者一个 HTTP 接口、IPC 频道）出发，往下顺着它用到的连下去，往上找用到它的。
    每一步按「跨进程的接口 > 函数调用 > import」挑最重要的几条（width），layer 是离起点几步（往上是负数）"""
    nodes = {str(n["id"]): n for n in graph.get("nodes", [])}
    test = {f for f, n in nodes.items() if n.get("test")}
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    into: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for link in graph.get("links", []):
        s, t = str(link["source"]), str(link["target"])
        if s in nodes and t in nodes and s not in test and t not in test and s != t:
            out[s].append(link)
            into[t].append(link)

    starts: list[str] = []
    key = endpoint.strip()
    if key:
        # 先找名字一模一样的接口；没有才按包含找（/agent/sessions 不要把 /agent/sessions/{}/messages 也带进来）
        exact = any(key == str(v) for links in out.values() for link in links for v in (link.get("via") or []))

        def carries(link: dict[str, Any]) -> bool:
            return any((key == str(v)) if exact else (key in str(v)) for v in (link.get("via") or []))

        for s, links in out.items():
            if any(carries(link) for link in links):
                starts.append(s)
                out[s] = [link for link in links if carries(link)]  # 第一步只顺着这个接口走
    elif _norm(start) in nodes:
        starts = [_norm(start)]
    if not starts:
        return {"start": start or endpoint, "nodes": [], "links": []}

    # 每个文件离「跨进程的接口」还有几步（自己就发 HTTP、IPC 的是 0）：往下走时优先走向接口的那条，
    # 不然链路会钻进 i18n、工具函数这些人人都用、却说明不了「这件事交给了谁」的文件
    reach: dict[str, int] = {f: 0 for f, links in out.items() if any(link["kind"] in ("http", "ipc") for link in links)}
    frontier = list(reach)
    while frontier:
        nxt = []
        for f in frontier:
            for link in into[f]:
                s = str(link["source"])
                if link["kind"] in ("call", "import") and s not in reach:
                    reach[s] = reach[f] + 1
                    nxt.append(s)
        frontier = nxt
    users = {f: len({str(link["source"]) for link in links}) for f, links in into.items()}

    def best(links: list[dict[str, Any]], far: str) -> list[dict[str, Any]]:
        own = [link for link in links if link["kind"] != "import"] or links  # 有真正的调用就不走 import
        downward = far == "target"
        ranked = sorted(own, key=lambda link: (
            KIND_ORDER.get(str(link["kind"]), 9),
            reach.get(str(link[far]), 99) if downward else 0,
            users.get(str(link[far]), 0) if downward else 0,  # 人人都用的（i18n、工具）排后面
            -int(link.get("weight") or 1), str(link[far])))
        picked: dict[str, dict[str, Any]] = {}
        for link in ranked:
            if str(link[far]) not in picked:
                picked[str(link[far])] = link
        return list(picked.values())[:width]

    layer: dict[str, int] = {s: 0 for s in starts}
    edges: dict[tuple[str, str, str], dict[str, Any]] = {}

    def walk(level: list[str], steps: int, sign: int) -> None:
        for depth in range(1, steps + 1):
            nxt: list[str] = []
            for f in level:
                for link in best(out[f] if sign > 0 else into[f], "target" if sign > 0 else "source"):
                    other = str(link["target"] if sign > 0 else link["source"])
                    edges[(str(link["source"]), str(link["target"]), str(link["kind"]))] = link
                    if other not in layer and len(layer) < MAX_CHAIN_NODES:
                        layer[other] = depth * sign
                        nxt.append(other)
            level = nxt

    walk(list(starts), down, 1)
    walk(list(starts), up, -1)
    return {
        "start": start or endpoint,
        "nodes": [{"id": f, "name": nodes[f]["name"], "group": nodes[f].get("group"), "lang": nodes[f].get("lang"),
                   "lines": nodes[f].get("lines"), "functions": nodes[f].get("functions"), "layer": d, "start": d == 0}
                  for f, d in sorted(layer.items(), key=lambda kv: (kv[1], kv[0]))],
        "links": [{"source": s, "target": t, "kind": k, "weight": link.get("weight", 1), "via": (link.get("via") or [])[:6]}
                  for (s, t, k), link in sorted(edges.items()) if s in layer and t in layer],
    }


def endpoints(graph: Graph, limit: int = 200) -> list[dict[str, Any]]:
    """项目里认出来的跨进程接口（HTTP 路径、IPC 频道）：选「从这个接口看链路」用"""
    found: dict[tuple[str, str], set[str]] = defaultdict(set)
    for link in graph.get("links", []):
        if link["kind"] in ("http", "ipc"):
            for via in link.get("via") or []:
                found[(str(link["kind"]), str(via))].add(str(link["target"]))
    return [{"kind": k, "name": v, "handlers": sorted(t)[:3]} for (k, v), t in sorted(found.items())][:limit]


def link_text(link: dict[str, Any]) -> str:
    """一条连线写成一句话：「a → b：调用 12 次、HTTP 接口（/agent/sessions）」"""
    kinds = link.get("kinds") or {str(link.get("kind")): int(link.get("weight") or 1)}
    parts = []
    for kind, weight in sorted(kinds.items(), key=lambda kv: KIND_ORDER.get(kv[0], 9)):
        parts.append(f"{KIND_TEXT.get(kind, kind)} {weight} 处")
    via = link.get("via") or []
    tail = f"（{'、'.join(map(str, via[:4]))}）" if via else ""
    return f"{link['source']} → {link['target']}：{'、'.join(parts)}{tail}"


__all__ = ["area", "chain", "endpoints", "entries", "hubs", "link_text"]
