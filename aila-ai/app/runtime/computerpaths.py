"""电脑任务的受保护位置。只检查路径和命令文字，不读取这些目录里的内容。"""

import ntpath
import os
import re
from pathlib import Path

from .workspace import Workspace

PROTECTED_PARTS = {
    "appdata", ".ssh", ".gnupg", ".mozilla", ".password-store", ".bitwarden", ".1password",
    "keepass", "keepassxc", "1password", "bitwarden", "user data",
}
PROFILE_PARTS = (
    ".config/google-chrome", ".config/chromium", ".config/microsoft-edge",
    ".config/mozilla", ".config/keepassxc", ".local/share/keyrings",
    "library/application support/google/chrome", "library/application support/microsoft edge",
    "library/application support/firefox", "library/keychains",
)


def normalized(path: str | Path) -> str:
    return ntpath.normpath(str(path).replace("/", "\\")).rstrip("\\").casefold()


def within(path: str | Path, root: str | Path) -> bool:
    child, parent = normalized(path), normalized(root)
    return child == parent or child.startswith(parent + "\\")


class ProtectedPaths:
    def __init__(self, home: Path, data_dir: Path | None = None, env_file: Path | None = None) -> None:
        self.home = home.resolve()
        profile = os.getenv("USERPROFILE") or os.getenv("HOME")
        self.use_environment = not profile or Path(profile).resolve() == self.home
        self.roots = [self.home / name for name in PROTECTED_PARTS]
        self.roots += [self.home / name for name in PROFILE_PARTS]
        # Windows 可以把 AppData 重定向到其他盘。
        if self.use_environment:
            self.roots += [Path(value) for name in ("APPDATA", "LOCALAPPDATA")
                           if (value := os.getenv(name))]
        self.files: list[Path] = []
        if data_dir is not None:
            data_dir = data_dir.resolve()
            self.roots.append(data_dir)
            self.files.append(data_dir.parent / ".env")
        if env_file is not None:
            self.files.append(env_file.resolve())

    def contains(self, path: Path) -> bool:
        # 名称判断保留未解析的路径，目标判断拦住普通名字的链接。
        if Workspace.is_secret(path) or path.suffix.casefold() in (".kdb", ".kdbx", ".opvault", ".agilekeychain"):
            return True
        parts = normalized(path).split("\\")
        if any(part in PROTECTED_PARTS for part in parts):
            return True
        candidate = normalized(path)
        if any(normalized(part) in candidate for part in PROFILE_PARTS):
            return True
        resolved = path.resolve()
        if any(within(resolved, root) for root in self.roots) or any(within(resolved, file) for file in self.files):
            return True
        try:
            return resolved.is_file() and resolved.stat().st_nlink > 1
        except OSError:
            return False

    def command_touches(self, command: str) -> bool:
        expanded = self.expand(command)
        # 动态变量和路径拼接无法可靠确定目标，不能当作已记住的普通命令。
        if re.search(r"[$%\x60]", expanded):
            return True
        plain = expanded.replace("\\", "/").casefold()
        if any(re.search(r"(^|[\s/:'\"=])" + re.escape(name) + r"(?=$|[\s/:'\"])", plain)
               for name in PROTECTED_PARTS):
            return True
        if any(part.casefold() in plain for part in PROFILE_PARTS):
            return True
        for root in [*self.roots, *self.files]:
            if normalized(root).replace("\\", "/") in plain:
                return True
        tokens = re.findall(r'"[^"]*"|\x27[^\x27]*\x27|[^\s;|<>(){}]+', expanded)
        recursive = bool(re.search(r"(?i)(?:-recurse\b|-depth\b|(?:^|\s)-[a-z]*r)", command))
        targets = 0
        for token in tokens[1:]:
            raw = token.strip("\"',")
            if "=" in raw:
                raw = raw.partition("=")[2].strip("\"',")
            if not raw or raw.startswith("-"):
                continue
            targets += 1
            candidate = Path(raw)
            path = candidate if candidate.is_absolute() else self.home / candidate
            if self.contains(path):
                return True
            # 递归或通配读取可能深入受保护子目录。
            if recursive and any(within(root, path) for root in self.roots):
                return True
        # 通配内容无法限定为普通文件。
        return (recursive and not targets) or bool(re.search(r"[*?\[\]]", expanded))

    def expand(self, text: str) -> str:
        values = {"USERPROFILE": str(self.home), "HOME": str(self.home)}
        values.update({name: str(self.home / suffix) for name, suffix in (
            ("APPDATA", "AppData/Roaming"), ("LOCALAPPDATA", "AppData/Local"),
        )})
        if self.use_environment:
            for name in ("APPDATA", "LOCALAPPDATA"):
                if value := os.getenv(name):
                    values[name] = value
        result = re.sub(r"(?i)\$HOME\b", lambda _: str(self.home), text)
        result = re.sub(r"(?i)%([a-z_]+)%", lambda m: values.get(m[1].upper(), m[0]), result)
        result = re.sub(r"(?i)\$env:([a-z_]+)", lambda m: values.get(m[1].upper(), m[0]), result)
        return re.sub(r"(?<!\w)~(?=[/\\]|$)", lambda _: str(self.home), result)
