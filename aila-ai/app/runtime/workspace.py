"""项目目录：工具碰文件之前都经过这里。

- 路径一律落在项目目录里（解析符号链接以后再判断），不能写 .git 里面
- 读过才能改，而且读过之后文件没被别人（比如你自己在编辑器里）改过：防止她拿着旧内容把你的改动覆盖掉
- 看起来存着密钥的文件（.env、私钥……）读之前要问：读了内容就会发给大模型
- 列文件：是 git 仓库就按 git 的规则（尊重 .gitignore），不是就跳过常见的依赖和构建目录
"""

import fnmatch
import hashlib
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

IGNORED_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache",
    "dist", "build", "out", "target", ".idea", ".gradle", ".next", ".nuxt", "coverage",
}
SECRET_NAME = re.compile(
    r"(^\.env(\..+)?$|\.pem$|\.key$|\.pfx$|\.p12$|^id_(rsa|dsa|ecdsa|ed25519)(\.pub)?$|credentials|secrets?\.(json|ya?ml|toml)$)",
    re.IGNORECASE,
)
SAFE_ENV_EXAMPLES = re.compile(r"^\.env\.(example|sample|template)$", re.IGNORECASE)
MAX_FILES = 20000


class WorkspaceError(Exception):
    """说给大模型听的错误"""


@dataclass
class FileState:
    mtime_ns: int
    size: int
    # 内容的指纹：同样大小、又在同一个时间刻度里改的（修改时间没变），只看时间会漏掉
    digest: str = ""


@dataclass
class Workspace:
    root: Path
    # 这个会话里读过（或者自己写过）的文件，以及当时的状态
    seen: dict[Path, FileState] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.root = self.root.resolve()

    # ---------- 路径 ----------

    def resolve(self, raw: str, for_write: bool = False) -> Path:
        if not raw or not raw.strip():
            raise WorkspaceError("没给文件路径。")
        p = Path(raw.strip())
        path = (p if p.is_absolute() else self.root / p).resolve()
        if path != self.root and not path.is_relative_to(self.root):
            raise WorkspaceError(f"{raw} 不在项目目录里，不能碰。")
        if for_write and any(os.path.normcase(part) == ".git" for part in path.relative_to(self.root).parts):
            raise WorkspaceError("不能改 .git 里面的文件。")
        if for_write and os.name == "nt" and any(":" in part for part in path.relative_to(self.root).parts):
            raise WorkspaceError("不能改文件的备用数据流。")
        return path

    def rel(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root).as_posix() or "."
        except ValueError:
            return str(path)

    @staticmethod
    def is_secret(path: Path) -> bool:
        return bool(SECRET_NAME.search(path.name)) and not SAFE_ENV_EXAMPLES.match(path.name)

    # ---------- 读过才能改 ----------

    def remember(self, path: Path, data: bytes | None = None) -> None:
        st = path.stat()
        digest = _digest(path) if data is None else digest_bytes(data)
        self.seen[path] = FileState(st.st_mtime_ns, st.st_size, digest)

    def check_fresh(self, path: Path) -> bytes:
        """改已有文件前检查读过的版本，返回这一版的原始字节。"""
        state = self.seen.get(path)
        if state is None:
            raise WorkspaceError(f"还没读过 {self.rel(path)}，先用 read_file 读一下再改。")
        changed = f"{self.rel(path)} 在你读过之后被改过（可能是用户自己改的），先重新读一下再改。"
        st = path.stat()
        if st.st_mtime_ns != state.mtime_ns or st.st_size != state.size:
            raise WorkspaceError(changed)
        data = path.read_bytes()
        if digest_bytes(data) != state.digest:
            raise WorkspaceError(changed)
        return data

    # ---------- 列文件 ----------

    def files(self, under: Path | None = None) -> list[Path]:
        """项目里的文件（不含被忽略的）。是 git 仓库时包括还没 git add 的新文件"""
        base = (under or self.root).resolve()
        listed = self._git_files(base)
        if listed is None:
            listed = self._walk(base)
        return listed[:MAX_FILES]

    def _git_files(self, base: Path) -> list[Path] | None:
        try:
            out = subprocess.run(
                ["git", "ls-files", "-co", "--exclude-standard", "-z"],
                cwd=base, capture_output=True, timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if out.returncode != 0:
            return None
        paths = [base / p for p in out.stdout.decode("utf-8", "replace").split("\0") if p]
        return [p for p in paths if p.is_file()]

    def _walk(self, base: Path) -> list[Path]:
        found: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS)
            for name in sorted(filenames):
                found.append(Path(dirpath) / name)
                if len(found) >= MAX_FILES:
                    return found
        return found


def glob_match(rel: str, pattern: str) -> bool:
    """「*.py」匹配任意目录下的 py 文件；带 / 的按整个相对路径匹配；** 可以跨目录"""
    pattern = pattern.strip().replace("\\", "/") or "*"
    if pattern.startswith("./"):
        pattern = pattern[2:]
    if "/" not in pattern:
        return fnmatch.fnmatch(rel.rsplit("/", 1)[-1], pattern)
    if fnmatch.fnmatch(rel, pattern):
        return True
    # a/**/b.py 也要匹配 a/b.py
    return "**/" in pattern and fnmatch.fnmatch(rel, pattern.replace("**/", ""))


def read_text(path: Path) -> tuple[str, str]:
    """读文本文件，返回（内容，编码）。认得 UTF-8（带不带 BOM）和 Windows 上常见的 GBK"""
    return decode_text(path.read_bytes(), path.name)


def decode_text(raw: bytes, name: str) -> tuple[str, str]:
    if b"\0" in raw[:8192]:
        raise WorkspaceError(f"{name} 是二进制文件，读不了。")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", "replace"), "utf-8-sig"
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("gbk"), "gbk"
    except UnicodeDecodeError:
        return raw.decode("utf-8", "replace"), "utf-8"


def write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    data = text.encode("utf-8") if encoding == "utf-8" else text.encode(encoding)
    if encoding == "utf-8-sig":
        data = b"\xef\xbb\xbf" + text.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def digest_bytes(data: bytes) -> str:
    return hashlib.blake2b(data, digest_size=16).hexdigest()


def _digest(path: Path) -> str:
    return digest_bytes(path.read_bytes())
