"""测试用的 MCP 服务（stdio，一行一条 JSON-RPC）：几个工具覆盖只读、会改东西、出错、慢、看环境变量。
用法：python fake_mcp_server.py [--crash-on-call] [--noisy]"""

import json
import os
import sys
import time

CRASH = "--crash-on-call" in sys.argv
NOISY = "--noisy" in sys.argv

TOOLS = [
    {"name": "echo", "description": "原样返回", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
     "annotations": {"readOnlyHint": True}},
    {"name": "write_note", "description": "记一条笔记（会改东西）", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}},
    {"name": "fail", "description": "总是出错", "inputSchema": {"type": "object"}},
    {"name": "slow", "description": "很慢", "inputSchema": {"type": "object", "properties": {"seconds": {"type": "number"}}}},
    {"name": "env", "description": "看环境变量", "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": True}},
    {"name": "weird name/with.dots", "description": "名字里有怪字符", "inputSchema": {"type": "object"}},
]


def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def reply(msg_id, result=None, error=None) -> None:
    send({"jsonrpc": "2.0", "id": msg_id, **({"error": error} if error else {"result": result})})


def text(value: str, error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": value}], "isError": error}


notes: list[str] = []
for line in sys.stdin:
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        continue
    method, msg_id, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if method is None:
        continue  # 客户端对我们请求的回应
    if method == "initialize":
        if NOISY:
            print("starting up (this goes to stdout by mistake)", flush=True)
        sys.stderr.write("fake server ready\n")
        sys.stderr.flush()
        reply(msg_id, {"protocolVersion": params.get("protocolVersion"), "capabilities": {"tools": {"listChanged": True}},
                       "serverInfo": {"name": "fake", "version": "1.0"}})
    elif method == "notifications/initialized":
        send({"jsonrpc": "2.0", "id": "srv-1", "method": "ping"})  # 服务端也会问客户端
        send({"jsonrpc": "2.0", "id": "srv-2", "method": "roots/list"})
    elif method == "tools/list":
        if params.get("cursor") == "page2":
            reply(msg_id, {"tools": TOOLS[3:]})
        else:
            reply(msg_id, {"tools": TOOLS[:3], "nextCursor": "page2"})
    elif method == "tools/call":
        if CRASH:
            sys.exit(3)
        name, args = params.get("name"), params.get("arguments") or {}
        if name == "echo":
            reply(msg_id, text(str(args.get("text", ""))))
        elif name == "write_note":
            notes.append(str(args.get("text", "")))
            reply(msg_id, text(f"记了 {len(notes)} 条"))
        elif name == "fail":
            reply(msg_id, text("数据库连不上", error=True))
        elif name == "slow":
            time.sleep(float(args.get("seconds", 5)))
            reply(msg_id, text("终于好了"))
        elif name == "env":
            keys = [k for k in ("LLM_API_KEY", "FAKE_TOKEN") if k in os.environ]
            reply(msg_id, text(",".join(keys) or "无"))
        elif name == "weird name/with.dots":
            reply(msg_id, {"content": [{"type": "image", "data": "", "mimeType": "image/png"}], "structuredContent": {"a": 1}})
        else:
            reply(msg_id, error={"code": -32602, "message": f"没有 {name}"})
    elif msg_id is not None:
        reply(msg_id, error={"code": -32601, "message": "不支持"})
