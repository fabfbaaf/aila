"""星图里的 import 关系和第三方库：每个文件 import 了谁。

- 项目里的文件：连成 import 光线（比按函数名猜的调用更准：同名函数、动态调用都不影响）
- 项目外的包：算成第三方库（标准库、Node 自带的模块不算），星图外圈画成「恒星」，看得出谁依赖了什么
- 只认字面写出来的 import（Python 的 import / from、JS/TS/Vue 的 import / export from / require / import()、
  Java 的 import），拼出来的、别名（@/ 这类）认不出就不连
"""

import re
import sys
from collections import Counter

PY_IMPORT = re.compile(r"^[ \t]*import[ \t]+([\w.]+(?:[ \t]*,[ \t]*[\w.]+)*)", re.M)
PY_FROM = re.compile(r"^[ \t]*from[ \t]+(\.*)([\w.]*)[ \t]+import[ \t]+\(?([\w*, \t]+)", re.M)
JS_SPEC = re.compile(
    r"""(?:\bimport\s+(?:[\w*{}\s,$]+\s+from\s+)?|\bexport\s+[\w*{}\s,$]+\s+from\s+|\brequire\(\s*|\bimport\(\s*)(['"])([^'"\n]+)\1"""
)
JAVA_IMPORT = re.compile(r"^[ \t]*import[ \t]+(static[ \t]+)?([\w.]+)(\.\*)?[ \t]*;", re.M)

NODE_BUILTINS = {
    "assert", "buffer", "child_process", "cluster", "crypto", "dgram", "dns", "events", "fs", "http", "http2", "https",
    "net", "os", "path", "perf_hooks", "process", "querystring", "readline", "stream", "string_decoder", "timers",
    "tls", "tty", "url", "util", "v8", "vm", "worker_threads", "zlib", "module", "inspector", "async_hooks",
}
PY_STDLIB = set(getattr(sys, "stdlib_module_names", ())) | {"__future__"}
WEB_EXTS = ("", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".jsx", ".vue", "/index.ts", "/index.tsx", "/index.js", "/index.vue")
FAMILY = {"python": "python", "java": "java", "javascript": "web", "typescript": "web", "tsx": "web", "vue": "web"}


def scan_imports(text: str, lang: str) -> list[str]:
    """文件里 import 的东西，原样记下来（解析要知道项目里有哪些文件，放到 resolve 里做）

    Python 记成「点数:模块:名字」（from . import x → 「1::x」）；JS 记路径；Java 记全名"""
    family = FAMILY.get(lang, "")
    out: list[str] = []
    if family == "python":
        for m in PY_IMPORT.finditer(text):
            out += [f"0:{name.strip()}:" for name in m.group(1).split(",") if name.strip()]
        for m in PY_FROM.finditer(text):
            names = [n.strip() for n in m.group(3).replace("(", "").split(",") if n.strip() and n.strip() != "*"]
            out.append(f"{len(m.group(1))}:{m.group(2)}:{','.join(n.split()[0] for n in names)}")
    elif family == "web":
        out += [m.group(2) for m in JS_SPEC.finditer(text)]
    elif family == "java":
        for m in JAVA_IMPORT.finditer(text):
            name = m.group(2)
            if m.group(1):  # import static a.b.C.method → a.b.C
                name = name.rsplit(".", 1)[0]
            out.append(name + (".*" if m.group(3) else ""))
    return out


def _java_name(path: str) -> str:
    """Java 文件 → 类的全名：从 src/main/java/、src/test/java/（或最后一个 java/）后面算起"""
    rel = path[:-5]
    if "/java/" in f"/{rel}":
        rel = f"/{rel}".rsplit("/java/", 1)[1]
    return rel.replace("/", ".")


def _dir(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _norm(path: str) -> str | None:
    parts: list[str] = []
    for p in path.split("/"):
        if p in ("", "."):
            continue
        if p == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(p)
    return "/".join(parts)


class Resolver:
    def __init__(self, files: list[str]) -> None:
        self.files = set(files)
        # Python：「a/b/c」（不带 .py、__init__ 记成包本身）→ 文件；绝对 import 按结尾对上
        self.py: dict[str, list[str]] = {}
        for f in files:
            if f.endswith(".py"):
                key = f[:-3]
                if key.endswith("/__init__"):
                    key = key[: -len("/__init__")]
                self.py.setdefault(key, []).append(f)
        self.java = {_java_name(f): f for f in files if f.endswith(".java")}
        self.java_packages = {k.rsplit(".", 1)[0] for k in self.java}

    def _py_abs(self, module: str) -> str | None:
        """a.b.c → 项目里的文件：按路径结尾对上，只有一个对得上才算"""
        tail = module.replace(".", "/")
        hits = [fs[0] for key, fs in self.py.items() if key == tail or key.endswith("/" + tail)]
        return hits[0] if len(hits) == 1 else None

    def python(self, src: str, spec: str) -> tuple[list[str], str | None]:
        dots, module, names = spec.split(":", 2)
        level = int(dots)
        if level:  # 相对 import：从这个文件所在的包往上 level-1 层
            base = _dir(src)
            for _ in range(level - 1):
                base = _dir(base)
            target = "/".join(p for p in (base, module.replace(".", "/")) if p)
            found = []
            for name in [""] + [n for n in names.split(",") if n]:
                key = "/".join(p for p in (target, name) if p)
                if key in self.py:
                    found.append(self.py[key][0])
            return (found[:1] if not names or module else found), None
        # from a.b import c：c 可能是子模块（a/b/c.py），先找它；都不是就是 a.b 本身
        hits = [h for n in names.split(",") if n and (h := self._py_abs(f"{module}.{n}" if module else n))]
        if not hits and module and (h := self._py_abs(module)):
            hits = [h]
        if hits:
            return list(dict.fromkeys(hits)), None
        top = module.split(".")[0]
        if not top or top in PY_STDLIB or self._py_abs(top):
            return [], None
        return [], top

    def web(self, src: str, spec: str) -> tuple[list[str], str | None]:
        if spec.startswith((".", "/")):
            base = _norm(f"{_dir(src)}/{spec}") if spec.startswith(".") else _norm(spec)
            if base is None:
                return [], None
            stems = [base, re.sub(r"\.(m|c)?js$", "", base)]
            for stem in stems:
                for ext in WEB_EXTS:
                    if stem + ext in self.files:
                        return [stem + ext], None
            return [], None
        if spec.startswith(("node:", "@/", "~", "virtual:", "#")) or ":" in spec:
            return [], None
        parts = spec.split("/")
        name = "/".join(parts[:2]) if spec.startswith("@") else parts[0]
        if name in NODE_BUILTINS or not re.fullmatch(r"(@[\w.-]+/)?[\w.-]+", name):
            return [], None
        return [], name

    def java_(self, spec: str) -> tuple[list[str], str | None]:
        if spec.endswith(".*"):
            if spec[:-2] in self.java_packages:
                return [], None
            spec = spec[:-2] + ".X"
        if spec in self.java:
            return [self.java[spec]], None
        parts = spec.split(".")
        if parts[0] in ("java", "javax", "jdk", "sun") or any(p.startswith(".".join(parts[:2])) for p in self.java_packages):
            return [], None
        return [], ".".join(parts[:2])


def resolve(found: dict[str, list[str]], langs: dict[str, str]) -> tuple[Counter[tuple[str, str]], dict[str, dict]]:
    """所有文件的 import → (项目里文件之间的 import 次数, 第三方库 → {生态, 用到它的文件})"""
    r = Resolver(list(langs))
    links: Counter[tuple[str, str]] = Counter()
    libs: dict[str, dict] = {}
    for src, specs in found.items():
        family = FAMILY.get(langs.get(src, ""), "")
        for spec in specs:
            if family == "python":
                targets, lib = r.python(src, spec)
                eco = "python"
            elif family == "web":
                targets, lib = r.web(src, spec)
                eco = "npm"
            elif family == "java":
                targets, lib = r.java_(spec)
                eco = "java"
            else:
                continue
            for t in targets:
                if t != src:
                    links[(src, t)] += 1
            if lib:
                entry = libs.setdefault(f"{eco}:{lib}", {"name": lib, "ecosystem": eco, "users": set()})
                entry["users"].add(src)
    return links, libs
