"""下载语音模型到 aila-ai/models/（不进 Git）。都来自 sherpa-onnx 官方发布页。

用法：
    .venv\\Scripts\\python scripts\\download_models.py              # 默认四个，约 370MB
    .venv\\Scripts\\python scripts\\download_models.py tts-kokoro   # 可选的合成引擎

已经下载过的会跳过。开着代理也能用（httpx 会读系统代理设置）。
"""

import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

import httpx

BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
MODELS_DIR = Path(__file__).resolve().parent.parent / "models"


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


def download(asset: Asset, client: httpx.Client) -> None:
    dest = MODELS_DIR / asset.target
    if dest.exists():
        print(f"已存在，跳过：{asset.target}")
        return

    name = asset.url.rsplit("/", 1)[1]
    with tempfile.TemporaryDirectory(dir=MODELS_DIR) as tmp:
        tmp_file = Path(tmp) / name
        with client.stream("GET", asset.url) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            done = 0
            with tmp_file.open("wb") as f:
                for chunk in resp.iter_bytes(1 << 20):
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        print(f"\r{name}：{done / 1e6:.0f} / {total / 1e6:.0f} MB", end="", flush=True)
        print()

        if asset.is_archive:
            extract_dir = Path(tmp) / "x"
            with tarfile.open(tmp_file, "r:bz2") as tar:
                tar.extractall(extract_dir, filter="data")  # data 过滤器：拒绝绝对路径、../ 和链接
            (extract_dir / asset.target).rename(dest)
        else:
            tmp_file.rename(dest)
    print(f"完成：{asset.target}")


def main(groups: list[str]) -> None:
    MODELS_DIR.mkdir(exist_ok=True)
    selected = ASSETS if not groups else [a for a in ASSETS + OPTIONAL if a.group in groups]
    if not selected:
        sys.exit(f"没有这一类：{groups}，可选 {sorted({a.group for a in ASSETS + OPTIONAL})}")
    with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(30, read=120)) as client:
        for asset in selected:
            download(asset, client)


if __name__ == "__main__":
    main(sys.argv[1:])
