"""示例扩展「骰子」的工具服务：一个最小的 MCP 服务，只用 Python 标准库。

MCP 的 stdio 传输：标准输入每行一条 JSON-RPC 请求，标准输出每行一条回复；日志写标准错误。
要做的只有：initialize、tools/list、tools/call，再回应 ping。写自己的扩展可以照这个改。
"""

import json
import os
import random
import sys

DEFAULT_SIDES = int(os.environ.get("DICE_SIDES") or 6)

TOOLS = [
    {
        "name": "roll",
        "description": "掷骰子。count 个，每个 sides 面（不填用设置里的默认值），返回每个的点数和总和。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "minimum": 1, "maximum": 20, "description": "掷几个，默认 1"},
                "sides": {"type": "integer", "minimum": 2, "maximum": 1000, "description": "几面，默认看设置"},
            },
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "pick",
        "description": "从给的几个选项里随机抽一个（帮人做决定）。",
        "inputSchema": {
            "type": "object",
            "properties": {"options": {"type": "array", "items": {"type": "string"}, "minItems": 1}},
            "required": ["options"],
        },
        "annotations": {"readOnlyHint": True},
    },
]


def call(name: str, args: dict) -> str:
    if name == "roll":
        count = max(1, min(20, int(args.get("count") or 1)))
        sides = max(2, min(1000, int(args.get("sides") or DEFAULT_SIDES)))
        rolls = [random.randint(1, sides) for _ in range(count)]
        return f"{count} 个 {sides} 面骰子：{rolls}，总和 {sum(rolls)}"
    if name == "pick":
        options = [str(o) for o in args.get("options") or [] if str(o).strip()]
        if not options:
            raise ValueError("options 不能是空的")
        return f"抽到了：{random.choice(options)}"
    raise ValueError(f"没有 {name} 这个工具")


def handle(msg: dict) -> dict | None:
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:  # 通知（比如 notifications/initialized）不用回
        return None
    if method == "initialize":
        result = {
            "protocolVersion": msg.get("params", {}).get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "aila-dice", "version": "1.0.0"},
        }
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = msg.get("params", {})
        try:
            text = call(params.get("name", ""), params.get("arguments") or {})
            result = {"content": [{"type": "text", "text": text}], "isError": False}
        except (ValueError, TypeError) as err:
            result = {"content": [{"type": "text", "text": str(err)}], "isError": True}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"不支持 {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    for line in sys.stdin.buffer:
        line = line.strip()
        if not line:
            continue
        try:
            reply = handle(json.loads(line))
        except json.JSONDecodeError:
            continue
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
