"""改 .env 文件：只改指定的键，注释、空行和其他配置原样保留。没有 .env 时从 .env.example 复制一份。"""

import os
import re
from pathlib import Path

_KEY = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")


def _format(value: str) -> str:
    # 有空格、井号、引号时加双引号，python-dotenv 能正确读回来
    if value == "" or re.search(r"[\s#\"'\\]", value):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def update(path: Path, values: dict[str, str]) -> None:
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    else:
        # 第一次保存：照着 .env.example 建一份（先找同一个文件夹里的，再找项目自带的），注释都在，以后手改也看得懂
        example = path.with_name(".env.example")
        if not example.exists():
            example = Path(__file__).resolve().parent.parent / ".env.example"
        lines = example.read_text(encoding="utf-8").splitlines() if example.exists() else []

    done: set[str] = set()
    for i, line in enumerate(lines):
        m = _KEY.match(line)
        if m and m.group(1) in values:
            lines[i] = f"{m.group(1)}={_format(values[m.group(1)])}"
            done.add(m.group(1))
    lines += [f"{k}={_format(v)}" for k, v in values.items() if k not in done]

    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)  # 写到一半断电也不会把原来的配置弄坏
