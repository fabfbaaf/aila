"""打包：app 里不是 .py 的文件（手机页面、使用说明……）都要写进 scripts/build_service.py 的 --add-data，
不然安装包里没有它们（PyInstaller 只收 Python 模块），装好以后那个功能就坏了"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_every_data_file_is_bundled() -> None:
    script = (ROOT / "scripts" / "build_service.py").read_text(encoding="utf-8")
    missing = []
    for f in (ROOT / "app").rglob("*"):
        if not f.is_file() or f.suffix in (".py", ".pyc") or "__pycache__" in f.parts:
            continue
        rel = f.relative_to(ROOT)
        # add-data 写的是文件本身或者它所在的文件夹（用 / 拼的 ROOT / 'app' / ... 形式）
        parts = [f"'{p}'" for p in rel.parts]
        folder = " / ".join(parts[:-1])
        if " / ".join(parts) not in script and f"ROOT / {folder}}}" not in script:
            missing.append(rel.as_posix())
    assert missing == [], f"这些文件没打进安装包：{missing}"


def test_bundled_models_count_as_present(tmp_path: Path) -> None:
    """安装包带着语音合成（melo）：设置页不说缺、下载时跳过、合成时找得到；你自己下了新的就用下载的"""
    from app.models_download import ASSETS, download, find, missing

    models, bundled = tmp_path / "models", tmp_path / "bundled"
    models.mkdir()
    (bundled / "vits-melo-tts-zh_en").mkdir(parents=True)
    assert "tts" in missing(models) and "tts" not in missing(models, bundled)
    assert find(models, bundled, "vits-melo-tts-zh_en") == bundled
    (models / "vits-melo-tts-zh_en").mkdir()
    assert find(models, bundled, "vits-melo-tts-zh_en") == models  # 下载的优先
    assert find(models, None, "kokoro-multi-lang-v1_1") == models
    said: list[str] = []
    tts = next(a for a in ASSETS if a.group == "tts")
    (models / "vits-melo-tts-zh_en").rmdir()
    download(tts, models, client=None, say=said.append, bundled=bundled)  # 带着的不下载（client 都用不到）
    assert said == ["安装包里带着，跳过：vits-melo-tts-zh_en"]
