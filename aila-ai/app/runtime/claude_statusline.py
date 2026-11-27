"""Claude Code 状态栏命令，写本地额度读数。只依赖 Python 标准库。"""

import json
import math
import os
import sys
import tempfile
from pathlib import Path

INPUT_LIMIT = 1024 * 1024


def fields(source: object, *keys: str) -> dict:
    if not isinstance(source, dict):
        return {}
    result = {}
    for key in keys:
        value = source.get(key)
        if type(value) in (int, float) and 0 <= value <= 1e15 and math.isfinite(value):
            result[key] = value
        elif key == "period" and value in ("daily", "weekly", "monthly"):
            result[key] = value
    return result


def collect(raw: bytes, folder: Path) -> str:
    data = json.loads(raw)
    if not isinstance(data, dict):
        return "Claude Code"
    limits = data.get("rate_limits")
    rate_limits = {}
    if isinstance(limits, dict):
        for key in ("five_hour", "seven_day"):
            window = fields(limits.get(key), "used_percentage", "resets_at")
            if "used_percentage" in window:
                rate_limits[key] = window
    spend = fields(data.get("spend_limit"), "used_usd", "limit_usd", "period", "resets_at")
    if not {"used_usd", "limit_usd"} <= spend.keys():
        spend = {}
    if rate_limits or spend:
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=folder, delete=False) as file:
            temp = Path(file.name)
            try:
                json.dump({"rate_limits": rate_limits, "spend_limit": spend}, file)
                file.close()
                os.replace(temp, folder / "claude-quota.json")
            finally:
                file.close()
                temp.unlink(missing_ok=True)
    five_hour = rate_limits.get("five_hour")
    return f'Claude Code 5h: {max(0, 100 - five_hour["used_percentage"]):g}% left' if five_hour else "Claude Code"


def main(args: list[str]) -> int:
    if len(args) != 1:
        return 2
    try:
        payload = sys.stdin.buffer.read(INPUT_LIMIT + 1)
        text = collect(payload, Path(args[0])) if len(payload) <= INPUT_LIMIT else "Claude Code"
    except (OSError, ValueError, RecursionError):
        text = "Claude Code"
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
