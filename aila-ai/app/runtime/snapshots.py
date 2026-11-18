"""保留编辑前的原字节，按日志里的指纹撤销。只恢复能确认仍是这一轮产物的文件。"""

import os
import re
import stat
import tempfile
from contextlib import suppress
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING

from .workspace import ComputerWorkspace, Workspace, WorkspaceError, digest_bytes

if TYPE_CHECKING:
    from .session import Event


FINGERPRINT = re.compile(r"[0-9a-f]{32}")


def _relative(rel: str) -> PurePosixPath:
    # Windows 的反斜杠是分隔符，在其他系统上可能是文件名原文。
    return PurePosixPath(rel.replace("\\", "/") if os.name == "nt" else rel)


class Snapshots:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._next: int | None = None

    def _directory(self) -> Path:
        path = self.root.parent.resolve() / self.root.name
        if path.resolve() != path:
            raise WorkspaceError("底片目录的位置变了。")
        return path

    def capture(self, before: bytes | None, after: bytes) -> dict[str, int | bool | str]:
        """在写之前存底片，返回只用于本地事件的元数据。"""
        metadata: dict[str, int | bool | str] = {"created": before is None, "after": digest_bytes(after)}
        if before is None:
            return metadata
        root = self._directory()
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self._next is None:
            self._next = max(
                (int(p.stem) for p in root.glob("*.bin") if p.stem.isascii() and p.stem.isdigit()),
                default=-1,
            ) + 1
        while True:
            index = self._next
            self._next += 1
            path = root / f"{index}.bin"
            try:
                flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
                fd = os.open(path, flags, 0o600)
            except FileExistsError:
                continue
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(before)
            except OSError:
                with suppress(OSError):
                    path.unlink()
                raise
            metadata.update(snap=index, before=digest_bytes(before))
            return metadata

    def discard(self, index: int) -> None:
        """写入未开始时产生的底片不需要保留。"""
        (self._directory() / f"{index}.bin").unlink(missing_ok=True)

    def _load(self, edit: "Event") -> bytes:
        index = edit.get("snap")
        if type(index) is not int or index < 0:
            raise WorkspaceError("这次修改没有留下底片。")
        path = self._directory() / f"{index}.bin"
        try:
            if path.resolve() != path or not stat.S_ISREG(path.lstat().st_mode):
                raise WorkspaceError("底片的位置变了。")
            data = path.read_bytes()
        except FileNotFoundError as err:
            raise WorkspaceError("找不到这次修改的底片。") from err
        expected = edit.get("before")
        if expected is not None and digest_bytes(data) != expected:
            raise WorkspaceError("底片内容变了。")
        return data

    def restore(self, workspace: Workspace, edits: "list[Event]") -> tuple[list[str], dict[str, str]]:
        """输入按日志先后排列，同一路径从最后一次修改追到这一轮开头。"""
        groups: dict[str, list[Event]] = {}
        skipped: dict[str, str] = {}
        restored: list[str] = []
        for edit in reversed(edits):
            rel = edit.get("file")
            if not isinstance(rel, str) or not rel:
                continue
            key = os.path.normcase(_relative(rel).as_posix())
            groups.setdefault(key, []).append(edit)
        for group in groups.values():
            rel = group[0]["file"]
            try:
                path = self._restore_file(workspace, rel, group)
            except WorkspaceError as err:
                skipped[rel] = str(err)
            except (OSError, RuntimeError, ValueError):
                skipped[rel] = "文件或底片无法访问。"
            else:
                restored.append(rel)
                workspace.seen.pop(path, None)
        return restored, skipped

    @staticmethod
    def _target(workspace: Workspace, rel: str) -> Path:
        name = _relative(rel)
        if isinstance(workspace, ComputerWorkspace):
            original = Path(rel)
            if (not name.parts or ".." in name.parts or (os.name == "nt" and PureWindowsPath(rel).drive
                                                       and not original.is_absolute())):
                raise WorkspaceError("这不是可撤销的电脑文件路径。")
            original = original if original.is_absolute() else workspace.root / original
            if workspace.is_secret(original):
                raise WorkspaceError("受保护位置不自动撤销。")
            path = workspace.resolve(rel, for_write=True)
            if path != original:
                raise WorkspaceError("文件指向的位置变了。")
            return path
        if (name.is_absolute() or (os.name == "nt" and PureWindowsPath(rel).drive)
                or not name.parts or ".." in name.parts or ".git" in (os.path.normcase(p) for p in name.parts)):
            raise WorkspaceError("这不是可撤销的项目文件路径。")
        if os.name == "nt" and any(":" in part for part in name.parts):
            raise WorkspaceError("不能撤销文件的备用数据流。")
        original = workspace.root.joinpath(*name.parts)
        if workspace.is_secret(original):
            raise WorkspaceError("可能存着密钥的文件不自动撤销。")
        path = workspace.resolve(name.as_posix(), for_write=True)
        # 日志已经记录了当时解析过的路径。后来出现链接，要保留用户新指向的文件。
        if path != original:
            raise WorkspaceError("文件指向的位置变了。")
        return path

    @staticmethod
    def _check_current(path: Path, expected: str) -> None:
        if not path.is_file():
            raise WorkspaceError("文件已经不在原位置。")
        if digest_bytes(path.read_bytes()) != expected:
            raise WorkspaceError("这一轮之后文件被改过。")

    def _restore_file(self, workspace: Workspace, rel: str, edits: "list[Event]") -> Path:
        path = self._target(workspace, rel)
        for edit in edits:
            after = edit.get("after")
            if type(edit.get("created")) is not bool or not isinstance(after, str) or not FINGERPRINT.fullmatch(after):
                raise WorkspaceError("这次修改没有可用的撤销记录。")
        for newer, older in zip(edits, edits[1:]):
            if newer["created"]:
                raise WorkspaceError("两次修改之间文件被替换过。")
            before = newer.get("before")
            if before is None:
                before = digest_bytes(self._load(newer))
            if before != older["after"]:
                raise WorkspaceError("两次修改之间文件被改过。")

        last, first = edits[0], edits[-1]
        self._check_current(path, last["after"])
        if first["created"]:
            path.unlink()
            return path
        original = self._load(first)
        mode = path.stat().st_mode & 0o777
        # 临时文件独占创建，原子替换时不跟随目标的硬链接，也不留下半写的文件。
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".aila-undo-", delete=False) as f:
            temp = Path(f.name)
            try:
                f.write(original)
                f.close()
                if os.name != "nt":
                    temp.chmod(mode)
                # 底片读取和临时文件写入期间，用户也可能保存文件。
                self._check_current(self._target(workspace, rel), last["after"])
                temp.replace(path)
            finally:
                f.close()
                with suppress(OSError):
                    temp.unlink(missing_ok=True)
        return path
