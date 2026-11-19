"""项目里「忽略这些文件夹」：代码检索和星图都不看这些文件（示例项目、生成的代码、测试用的数据……）。

两处来源，合起来用：
- 项目根目录的 `.ailaignore`：跟着项目走，可以提交进仓库，大家共用
- 「项目」页里给这个项目加的：只存在这台电脑上

一行一个，# 开头是注释。写法：
- `aila-ai/eval/tasks`、`docs/` ：从项目根目录算的路径，这个文件或文件夹下面的都不看
- `fixtures` ：不带斜杠的名字，哪一层的文件夹（或文件）叫这个都不看
- `*.min.js`、`src/generated/*` ：带 * ? [ ] 的按通配符对整个路径
"""

import fnmatch
from pathlib import Path

IGNORE_FILE = ".ailaignore"
MAX_PATTERNS = 100
MAX_LENGTH = 200


def clean(patterns: list[str]) -> list[str]:
    """去掉空行、注释、重复的，统一成正斜杠；太多、太长的截掉"""
    out: list[str] = []
    for raw in patterns:
        p = str(raw).strip().replace("\\", "/")
        if not p or p.startswith("#") or len(p) > MAX_LENGTH or ".." in p.split("/"):
            continue
        p = p.lstrip("./").rstrip("/") if not p.startswith("*") else p.rstrip("/")
        if p and p not in out:
            out.append(p)
    return out[:MAX_PATTERNS]


def read_file(root: Path) -> list[str]:
    try:
        return clean((root / IGNORE_FILE).read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        return []


def matches(rel: str, pattern: str) -> bool:
    """rel 是从项目根目录算的路径（正斜杠）"""
    parts = rel.split("/")
    if any(c in pattern for c in "*?["):
        if "/" in pattern:  # 带路径的通配符：对整个路径（* 也能跨过斜杠）
            return fnmatch.fnmatchcase(rel, pattern) or fnmatch.fnmatchcase(rel, pattern + "/*")
        return any(fnmatch.fnmatchcase(part, pattern) for part in parts)  # *.min.js：哪一层叫这个都算
    if "/" not in pattern:
        return pattern in parts
    return rel == pattern or rel.startswith(pattern + "/")


def ignored(rel: str, patterns: list[str]) -> bool:
    return any(matches(rel, p) for p in patterns)
