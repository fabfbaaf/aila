"""从报错文字里找出调用栈：哪个文件、第几行、哪个函数。

认得这些格式：
- Python：  File "C:\\work\\app.py", line 12, in main
- Node / 浏览器：  at run (C:\\work\\src\\a.ts:3:7) / at http://localhost:5173/src/App.vue?t=1:45:10
- Java：  at com.aila.server.sync.SyncService.saveMessages(SyncService.java:88)
- tsc：  src/main.ts(3,7): error TS2322 ...；vitest / eslint：src/a.ts:3:7
- pytest：  tests/test_app.py:12: in test_total

返回的顺序是「离出错点最近的在前」：Python 的 Traceback 最后一帧才是出错的地方，要倒过来；
Node 和 Java 第一帧就是。
"""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Frame:
    file: str  # 原样的路径（可能是绝对路径、相对路径、只有文件名）
    line: int
    func: str | None = None
    java_class: str | None = None  # Java 帧：带包名的类，用来拼出路径


PY_FRAME = re.compile(r'File "(?P<file>[^"]+)", line (?P<line>\d+)(?:, in (?P<func>[\w<>.]+))?')
NODE_FRAME = re.compile(
    r"\bat (?:(?P<func>[\w$.<>\[\] ]+?) \()?"
    r"(?P<file>(?:file:///|https?://[^/\s]+/)?(?:[A-Za-z]:)?[^():\s]*?\.(?:[cm]?[jt]sx?|vue))(?:\?[^:\s)]*)?"
    r":(?P<line>\d+):\d+\)?"
)
JAVA_FRAME = re.compile(r"\bat (?P<method>[\w$.<>]+)\((?P<file>[\w$]+\.java):(?P<line>\d+)\)")
TSC = re.compile(r"(?P<file>[\w./\\@-]+\.(?:[cm]?[jt]sx?|vue))\((?P<line>\d+),\d+\)")
COLON = re.compile(r"(?<![\w/\\.])(?P<file>(?:[A-Za-z]:)?[\w./\\@-]+\.(?:py|[cm]?[jt]sx?|vue|java)):(?P<line>\d+)(?::\d+)?\b")


def parse_frames(text: str) -> list[Frame]:
    frames: list[Frame] = []

    py = [Frame(m["file"], int(m["line"]), m["func"]) for m in PY_FRAME.finditer(text)]
    frames += reversed(py)  # Python：最后一帧是出错的地方

    for m in JAVA_FRAME.finditer(text):
        method = m["method"]
        cls = method.rsplit(".", 1)[0] if "." in method else None
        frames.append(Frame(m["file"], int(m["line"]), method.rsplit(".", 1)[-1], cls))

    for m in NODE_FRAME.finditer(text):
        frames.append(Frame(_clean_path(m["file"]), int(m["line"]), (m["func"] or "").strip() or None))

    taken = {(f.file, f.line) for f in frames}
    for pattern in (TSC, COLON):
        for m in pattern.finditer(text):
            f = Frame(_clean_path(m["file"]), int(m["line"]))
            if (f.file, f.line) not in taken and not _inside_taken(f, frames):
                frames.append(f)
                taken.add((f.file, f.line))

    unique: list[Frame] = []
    seen = set()
    for f in frames:
        key = (f.file, f.line)
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


def _clean_path(raw: str) -> str:
    """file:///C:/x.ts → C:/x.ts；http://localhost:5173/src/App.vue → src/App.vue"""
    if raw.startswith("file:///"):
        return raw[len("file:///"):]
    if raw.startswith(("http://", "https://")):
        return urlsplit(raw).path.lstrip("/")
    return raw


def _inside_taken(f: Frame, frames: list[Frame]) -> bool:
    """「at run (C:\\a.ts:3:7)」已经认过了，别再按「a.ts:3」认一遍"""
    return any(g.line == f.line and (g.file.endswith(f.file) or f.file.endswith(g.file)) for g in frames)
