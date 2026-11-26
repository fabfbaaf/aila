"""下载语音模型：缺哪些、进度行的格式（桌面端按行解析）、命令行入口。不连网：用假的下载客户端。"""

import io
import tarfile
from contextlib import contextmanager
from pathlib import Path

import pytest

from app import models_download as md
from app.__main__ import main as entry


class FakeResponse:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.headers = {"content-length": str(len(data))}

    def raise_for_status(self) -> None:
        pass

    def iter_bytes(self, size: int):
        for i in range(0, len(self.data), size):
            yield self.data[i:i + size]


class FakeClient:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.asked: list[str] = []

    @contextmanager
    def stream(self, method: str, url: str):
        self.asked.append(url)
        yield FakeResponse(self.files[url.rsplit("/", 1)[1]])


def archive(top: str) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:bz2") as tar:
        data = b"model"
        info = tarfile.TarInfo(f"{top}/model.onnx")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_missing_lists_what_is_not_downloaded(tmp_path: Path) -> None:
    assert md.missing(tmp_path) == ["kws", "vad", "asr", "tts"]
    (tmp_path / "silero_vad.onnx").write_bytes(b"x")
    (tmp_path / "vits-melo-tts-zh_en").mkdir()
    assert md.missing(tmp_path) == ["kws", "asr"]


def test_download_reports_progress_and_unpacks(tmp_path: Path) -> None:
    tts = next(a for a in md.ASSETS if a.group == "tts")
    vad = next(a for a in md.ASSETS if a.group == "vad")
    big = b"\0" * 12_000_000
    client = FakeClient({tts.url.rsplit("/", 1)[1]: archive(tts.target), vad.url.rsplit("/", 1)[1]: big})
    lines: list[str] = []
    md.download(tts, tmp_path, client, lines.append)
    md.download(vad, tmp_path, client, lines.append)

    assert (tmp_path / tts.target / "model.onnx").read_bytes() == b"model"
    assert (tmp_path / vad.target).stat().st_size == len(big)
    # 进度行：「进度 名字 已下MB 总MB」，每 5MB 一条，最后一条是下完的
    progress = [line for line in lines if line.startswith("进度 silero_vad.onnx")]
    assert progress == ["进度 silero_vad.onnx 5 12", "进度 silero_vad.onnx 10 12", "进度 silero_vad.onnx 12 12"]
    assert f"完成：{tts.target}" in lines and f"解压：{tts.target}" in lines
    # 临时文件不留下
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([tts.target, vad.target])

    # 再下一次：跳过
    client.asked.clear()
    md.download(vad, tmp_path, client, lines.append)
    assert client.asked == [] and lines[-1] == f"已存在，跳过：{vad.target}"


def test_unknown_group_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="没有这一类"):
        md.run(["nope"], tmp_path)


def test_entry_routes_download_flag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys) -> None:
    got: list[tuple[list[str], Path]] = []
    monkeypatch.setenv("AILA_MODELS_DIR", str(tmp_path))
    monkeypatch.setattr(md, "run", lambda groups, models_dir, say=None, bundled=None: got.append((groups, models_dir)))
    assert entry(["--download-models", "tts-kokoro"]) == 0
    assert got == [(["tts-kokoro"], tmp_path)]

    def boom(*a, **k):
        raise RuntimeError("网断了")

    monkeypatch.setattr(md, "run", boom)
    assert entry(["--download-models"]) == 1
    assert "下载失败：网断了" in capsys.readouterr().err
