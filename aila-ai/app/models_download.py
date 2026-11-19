"""下载语音模型和向量模型。都来自 sherpa-onnx 官方发布页（向量模型由 fastembed 从 Hugging Face 下）。

为什么放在 app/ 里：安装包里没有 Python 和 scripts/，桌面端用打包好的 aila-ai.exe --download-models
来下载；开发时还是用 scripts/download_models.py，两边调同一个函数。

已经下载过的会跳过；下到一半断了，临时文件在临时目录里，下次重新下。开着代理也能用（httpx 会读系统代理设置）。
进度每行一条，格式固定（「进度 名字 已下 总共」，单位 MB），桌面端按行解析显示在设置页。
"""

import sys
import tarfile
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download"


@dataclass
class Asset:
    group: str
    url: str
    # 解压后的目录名，或单个文件名；存在就跳过
    target: str

    @property
    def is_archive(self) -> bool:
        return self.url.endswith(".tar.bz2")


ASSETS = [
    # 唤醒词：中英文关键词识别，流式，3M 参数
    Asset("kws", f"{BASE}/kws-models/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20.tar.bz2",
          "sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20"),
    # 断句：判断用户什么时候开始、结束说话
    Asset("vad", f"{BASE}/asr-models/silero_vad.onnx", "silero_vad.onnx"),
    # 识别：SenseVoice，中英日韩粤，非流式但很快（一句话一两百毫秒）。
    # 用 2024-07-17 版：实测 2025-09-09 版在 sherpa-onnx 2.0 下语种参数不生效，全部当成粤语识别
    Asset("asr", f"{BASE}/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2",
          "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"),
    # 合成：MeloTTS，中英混读，CPU 上一句约 0.2–0.6 秒
    Asset("tts", f"{BASE}/tts-models/vits-melo-tts-zh_en.tar.bz2", "vits-melo-tts-zh_en"),
]

# 可选的合成引擎（AILA_TTS_ENGINE），要用时单独下载
OPTIONAL = [
    # Kokoro：上百种音色，CPU 上比 MeloTTS 慢一倍（int8 量化版在 CPU 上反而更慢，不用）
    Asset("tts-kokoro", f"{BASE}/tts-models/kokoro-multi-lang-v1_1.tar.bz2", "kokoro-multi-lang-v1_1"),
    # Matcha：最快，但只会中文，训练数据只许非商业使用
    Asset("tts-matcha", f"{BASE}/tts-models/matcha-icefall-zh-baker.tar.bz2", "matcha-icefall-zh-baker"),
    Asset("tts-matcha", f"{BASE}/vocoder-models/vocos-22khz-univ.onnx", "vocos-22khz-univ.onnx"),
]

Say = Callable[[str], None]


def _say(line: str) -> None:
    print(line, flush=True)


def download(asset: Asset, models_dir: Path, client, say: Say = _say) -> None:
    dest = models_dir / asset.target
    if dest.exists():
        say(f"已存在，跳过：{asset.target}")
        return

    name = asset.url.rsplit("/", 1)[1]
    with tempfile.TemporaryDirectory(dir=models_dir) as tmp:
        tmp_file = Path(tmp) / name
        with client.stream("GET", asset.url) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            done = 0
            shown = -1
            with tmp_file.open("wb") as f:
                for chunk in resp.iter_bytes(1 << 20):
                    f.write(chunk)
                    done += len(chunk)
                    mb = done // 1_000_000
                    if mb != shown and (mb % 5 == 0 or done == total):  # 每 5MB 报一次，免得刷屏
                        shown = mb
                        say(f"进度 {asset.target} {mb} {total // 1_000_000}")

        if asset.is_archive:
            say(f"解压：{asset.target}")
            extract_dir = Path(tmp) / "x"
            with tarfile.open(tmp_file, "r:bz2") as tar:
                tar.extractall(extract_dir, filter="data")  # data 过滤器：拒绝绝对路径、../ 和链接
            (extract_dir / asset.target).rename(dest)
        else:
            tmp_file.rename(dest)
    say(f"完成：{asset.target}")


def download_embedder(models_dir: Path, say: Say = _say) -> None:
    """向量模型（记忆、知识库用）：bge-small-zh，约 90MB，由 fastembed 从 Hugging Face 下载"""
    if (models_dir / "embed").exists():
        say("已存在，跳过：embed")
        return
    say("下载向量模型 BAAI/bge-small-zh-v1.5 ……")
    from .memory.embedder import FastEmbedder

    FastEmbedder(models_dir).embed(["测试一下"])
    say("完成：embed")


def missing(models_dir: Path) -> list[str]:
    """默认的语音模型里还没下载的（按类别：kws、vad、asr、tts）"""
    return [a.group for a in ASSETS if not (models_dir / a.target).exists()]


def groups_of() -> list[str]:
    return sorted({a.group for a in ASSETS + OPTIONAL} | {"embed"})


def run(groups: list[str], models_dir: Path, say: Say = _say) -> None:
    """groups 为空：默认的语音四个 + 向量模型（约 460MB）；否则只下指定的几类"""
    import httpx

    models_dir.mkdir(parents=True, exist_ok=True)
    selected = ASSETS if not groups else [a for a in ASSETS + OPTIONAL if a.group in groups]
    if not selected and groups != ["embed"]:
        raise ValueError(f"没有这一类：{groups}，可选 {groups_of()}")
    with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(30, read=120)) as client:
        for asset in selected:
            download(asset, models_dir, client, say)
    if not groups or "embed" in groups:
        download_embedder(models_dir, say)
    say("全部完成")


def main(argv: list[str]) -> int:
    from .config import load_settings

    try:
        run(argv, load_settings().models_dir)
    except Exception as e:  # noqa: BLE001 —— 命令行入口：说清楚哪里错了，返回非零
        print(f"下载失败：{e}", file=sys.stderr, flush=True)
        return 1
    return 0
