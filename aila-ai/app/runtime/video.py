"""本地短视频渲染。Windows 首次使用时准备固定版本的便携运行环境，之后复用缓存。"""

import asyncio
import hashlib
import json
import os
import platform
import shutil
import sys
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .shell import clean_env, run_argv
from .tools import CodingTool, Outcome, _obj, _str, report_progress
from .workspace import Workspace, WorkspaceError

HYPERFRAMES = "hyperframes@0.9.102"
MAX_ARCHIVE = 256 * 1024 * 1024
NPM_CLI = Path("node_modules/npm/bin/npm-cli.js")
FFMPEG_BINS = ("ffmpeg.exe", "ffprobe.exe")


@dataclass(frozen=True)
class Archive:
    folder: str
    url: str
    sha256: str


NODE = Archive(
    "node-v24.22.3-win-x64",
    "https://nodejs.org/dist/v24.22.3/node-v24.22.3-win-x64.zip",
    "e612af99f626ab415ea225af6bce5c971feb448cb5cae250bb1c3fa50c5f7d9f",
)
FFMPEG = Archive(
    "ffmpeg-9.1-essentials_build",
    "https://github.com/GyanD/codexffmpeg/releases/download/9.1/ffmpeg-9.1-essentials_build.zip",
    "b9bbbb3acece000cbeec994f856649354e52c773807841e78908374b24d658fd",
)


def _download(archive: Archive, destination: Path) -> None:
    digest = hashlib.sha256()
    size = 0
    with urllib.request.urlopen(archive.url, timeout=45) as response, destination.open("wb") as out:
        while data := response.read(256 * 1024):
            size += len(data)
            if size > MAX_ARCHIVE:
                raise ValueError("视频依赖超过预期大小。")
            digest.update(data)
            out.write(data)
    if digest.hexdigest() != archive.sha256:
        raise ValueError("视频依赖校验没通过，稍后可以再试。")


def _prepare_archive(root: Path, archive: Archive, files: tuple[str, ...], package: str = "") -> Path:
    target = root / archive.folder
    if all((target / rel).is_file() for rel in files):
        return target
    with TemporaryDirectory(prefix="prepare-", dir=root) as tmp:
        stage = Path(tmp) / "payload"
        stage.mkdir()
        source = Path(tmp) / "runtime.zip"
        _download(archive, source)
        prefix = archive.folder + "/"
        with zipfile.ZipFile(source) as z:
            for item in z.infolist():
                if not item.filename.startswith(prefix) or item.is_dir():
                    continue
                rel = item.filename.removeprefix(prefix)
                if rel not in files and not (package and rel.startswith(package)):
                    continue
                dest = (stage / rel).resolve()
                if not dest.is_relative_to(stage.resolve()):
                    raise ValueError("视频依赖的压缩包里有异常路径。")
                dest.parent.mkdir(parents=True, exist_ok=True)
                with z.open(item) as src, dest.open("wb") as out:
                    shutil.copyfileobj(src, out)
        if not all((stage / rel).is_file() for rel in files):
            raise ValueError("视频依赖的压缩包里少了文件。")
        try:
            stage.rename(target)
        except OSError:
            if not all((target / rel).is_file() for rel in files):
                raise
    return target


class VideoRuntime:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._preparing = asyncio.Lock()

    async def _command(self) -> tuple[list[str], str, dict[str, str]]:
        self.root.mkdir(parents=True, exist_ok=True)
        env = clean_env()
        env.update({
            "HYPERFRAMES_NO_TELEMETRY": "1",
            "npm_config_cache": str(self.root / "npm-cache"),
            "npm_config_registry": "https://registry.npmjs.org",
        })
        args = ["exec", "--yes", "--ignore-scripts", "--package", HYPERFRAMES, "--", "hyperframes"]
        if sys.platform == "win32" and platform.machine().lower() in ("amd64", "x86_64"):
            async with self._preparing:
                report_progress("正在准备本地视频工具")
                node = await asyncio.to_thread(_prepare_archive, self.root, NODE,
                                               ("node.exe", NPM_CLI.as_posix()), "node_modules/npm/")
                ffmpeg = await asyncio.to_thread(_prepare_archive, self.root, FFMPEG,
                                                 tuple("bin/" + name for name in FFMPEG_BINS))
            env["PATH"] = os.pathsep.join((str(node), str(ffmpeg / "bin"), env.get("PATH", "")))
            ffprobe = str(ffmpeg / "bin/ffprobe.exe")
            env["HYPERFRAMES_FFMPEG_PATH"] = str(ffmpeg / "bin/ffmpeg.exe")
            env["HYPERFRAMES_FFPROBE_PATH"] = ffprobe
            return [str(node / "node.exe"), str(node / NPM_CLI), *args], ffprobe, env
        npm = shutil.which("npm")
        ffprobe = shutil.which("ffprobe")
        if not npm or not ffprobe or not shutil.which("ffmpeg"):
            raise ValueError("这个系统上需要已有的 Node.js 22+ 和 FFmpeg（含 ffprobe）才能渲染。")
        return [npm, *args], ffprobe, env

    def tool(self, workspace: Workspace) -> CodingTool:
        def paths(args: dict[str, Any]) -> tuple[Path, Path]:
            project = workspace.resolve(str(args.get("project", "")), for_write=True)
            output = workspace.resolve(str(args.get("output", "")), for_write=True)
            entry = (project / "index.html").resolve()
            if not entry.is_file() or not entry.is_relative_to(project) or workspace.is_secret(entry):
                raise WorkspaceError("这个视频工程需要自己的 index.html。")
            if not output.is_relative_to(project) or workspace.is_secret(output) or output.suffix.lower() != ".mp4":
                raise WorkspaceError("把 MP4 保存到视频工程里，文件名用 .mp4。")
            if not output.parent.is_dir() or output.exists():
                raise WorkspaceError("要用一个新的 MP4 文件名，所在的文件夹得已经存在。")
            return project, output

        def preview(args: dict[str, Any]) -> str:
            try:
                project, output = paths(args)
                return f"工程：{project}\n导出：{output}\n首次使用会从官方来源准备本地渲染工具。"
            except (OSError, WorkspaceError, ValueError) as err:
                return str(err)

        async def render(args: dict[str, Any]) -> Outcome:
            try:
                project, output = paths(args)
                cli, ffprobe, env = await self._command()
                report_progress("正在检查视频工程")
                check = await run_argv([*cli, "check", str(project)], self.root, 240, env=env)
                if check.exit_code != 0:
                    return Outcome("视频检查没通过。以下是渲染器输出：\n" + check.render(240), ok=False)
                with TemporaryDirectory(prefix=".aila-render-", dir=output.parent) as tmp:
                    staged = Path(tmp) / "video.mp4"
                    report_progress("正在渲染 MP4")
                    result = await run_argv([*cli, "render", str(project), "--quality", "delivery", "-o", str(staged)],
                                            self.root, 600, env=env)
                    if result.exit_code != 0 or not staged.is_file():
                        return Outcome("视频渲染没完成。以下是渲染器输出：\n" + result.render(600), ok=False)
                    probe = await run_argv([ffprobe, "-v", "error", "-show_entries",
                                            "format=duration:stream=codec_type,width,height", "-of", "json", str(staged)],
                                           project, 20, env=env)
                    if probe.exit_code != 0:
                        return Outcome("渲染完成，但读取不到有效的视频文件。", ok=False)
                    data = json.loads(probe.output)
                    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
                    duration = float(data.get("format", {}).get("duration", 0))
                    if not video or duration <= 0 or not video.get("width") or not video.get("height"):
                        return Outcome("渲染文件缺少有效的视频轨。", ok=False)
                    # 同一个目标被两个任务同时选择时，只让先完成的写入。
                    if sys.platform == "win32":
                        staged.rename(output)
                    else:
                        os.link(staged, output)
                return Outcome(f"MP4 已保存到 {output}\n{video['width']} × {video['height']}，{duration:.2f} 秒。")
            except (OSError, WorkspaceError, ValueError, zipfile.BadZipFile) as err:
                return Outcome(f"视频没导出：{err}", ok=False)

        return CodingTool(
            "render_video", "检查视频工程并在本机渲染 MP4，首次使用自动准备本地工具。输出不会覆盖现有文件。",
            _obj({"project": _str("含 index.html 的视频工程目录"),
                  "output": _str("视频工程内的目标 .mp4 路径，相对任务工作目录或绝对路径")}, ["project", "output"]),
            render, lambda _: "video", lambda _: "渲染短视频", preview,
        )
