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
