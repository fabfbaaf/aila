"""会话：只追加的事件日志，发给模型的消息全部由日志推出来。

为什么这样存：
- 终端和桌面看的是同一份记录，任务中断（关了窗口、服务重启）后可以接着做
- 只追加 → 每一步发给模型的消息，开头和上一步完全一样，DeepSeek 的前缀缓存就能命中。
  所以这里绝不回头改旧消息：不滑动截断历史，不改写旧的工具结果；
  大的工具输出在记下时就截断好，压缩上下文也是追加一条 compaction 事件

事件（每行一个 JSON，都有 seq 和 t）：
  start        会话开始：项目目录、模型、权限模式、开头的环境说明（context，之后不变）
  user         用户说的话
  assistant    模型一步的输出：文字、思考过程、要调用的工具
  tool         一个工具调用的结果（status：ok / error / denied / rejected / interrupted）
  usage        一步的用量（命中缓存的输入、没命中的输入、输出）
  confirm / confirm_end   问用户、用户的回答
  todo         待办清单的最新状态
  compaction   上下文压缩：summary 代替 upto 之前的事件
  mode         权限模式变了
  title        用户改了会话标题（不进模型消息）
  turn_end     一轮结束（done / max_steps / interrupted / error）
  merge        在新分支上做时：跑测试、合并回你的代码的结果
  branch_closed  分支放弃了（这个会话之后不再接任务）
"""

import json
import posixpath
import re
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ..llm import Message, Usage

Event = dict[str, Any]

SESSION_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
# 这些事件会变成发给模型的消息；其余的只给界面看
MESSAGE_EVENTS = {"user", "assistant", "tool"}


class Session:
    def __init__(self, path: Path, events: list[Event]) -> None:
        self.path = path
        self.events = events
        start = events[0]
        self.id: str = start["id"]
        self.cwd = Path(start["cwd"])
        self.context: str = start.get("context", "")

    # ---------- 写 ----------

    def append(self, event: Event) -> Event:
        event = {**event, "seq": len(self.events), "t": round(time.time(), 3)}
        self.events.append(event)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    # ---------- 读 ----------

    @property
    def mode(self) -> str:
        for e in reversed(self.events):
            if e["type"] in ("mode", "start") and e.get("mode"):
                return str(e["mode"])
        return "ask"

    @property
    def branch(self) -> dict[str, Any] | None:
        """在新分支上做时：分支的信息（合并过的话 base 是最新一次合并的提交）；放弃了或者不在分支上是 None"""
        info = self.events[0].get("branch")
        if not info:
            return None
        info = dict(info)
        for e in self.events:
            if e["type"] == "merge" and e.get("merged"):
                info["base"] = e["base"]
            elif e["type"] == "branch_closed":
                return None
        return info

    @property
    def branch_closed(self) -> bool:
        return any(e["type"] == "branch_closed" for e in self.events)

    @property
    def workdir(self) -> Path:
        """她实际干活的目录：在新分支上做时是临时目录（worktree），否则就是项目目录"""
        info = self.events[0].get("branch")
        if not info:
            return self.cwd
        root = Path(info["worktree"])
        return root / info["sub"] if info.get("sub") else root

    @property
    def title(self) -> str:
        for e in reversed(self.events):
            if e["type"] == "title":
                return str(e["title"])
        first = next((e["text"] for e in self.events if e["type"] == "user"), "")
        line = first.strip().splitlines()[0] if first.strip() else "（还没开始）"
        return line[:60]

    @property
    def usage(self) -> Usage:
        total = Usage()
        for e in self.events:
            if e["type"] == "usage":
                total = total + Usage(e["prompt"], e["completion"], e["cache_hit"], e["cache_miss"], e.get("estimated", False))
        return total

    @property
    def last_prompt_tokens(self) -> int:
        """最近一步的输入长度：决定要不要压缩上下文"""
        for e in reversed(self.events):
            if e["type"] == "compaction":
                return 0
            if e["type"] == "usage" and e.get("purpose") in (None, "compaction"):  # 子 Agent 的上下文是它自己的
                return int(e["prompt"])
        return 0

    def unanswered_calls(self) -> list[dict[str, str]]:
        """最后一条 assistant 里还没有结果的工具调用（打断时要补上，不然下次请求会被模型服务拒绝）"""
        last = next((i for i in range(len(self.events) - 1, -1, -1) if self.events[i]["type"] == "assistant"), None)
        if last is None:
            return []
        done = {e["id"] for e in self.events[last + 1 :] if e["type"] == "tool"}
        return [c for c in self.events[last].get("tool_calls", []) if c["id"] not in done]

    def last_message_role(self) -> str | None:
        for e in reversed(self.events):
            if e["type"] in MESSAGE_EVENTS:
                return "user" if e["type"] == "user" else "assistant" if e["type"] == "assistant" else "tool"
        return None

    def messages(self, system: str) -> list[Message]:
        return derive_messages(system, self.events)

    def summary(self) -> dict[str, Any]:
        u = self.usage
        return {
            "id": self.id,
            "cwd": str(self.cwd),
            "title": self.title,
            "mode": self.mode,
            "model": self.events[0].get("model", ""),
            "created": self.events[0].get("created"),
            "updated": self.events[-1].get("t"),
            "events": len(self.events),
            "branch": self.branch["branch"] if self.branch else None,
            "usage": {"prompt": u.prompt, "completion": u.completion, "cache_hit": u.cache_hit, "cache_miss": u.cache_miss},
        }


def derive_messages(system: str, events: list[Event]) -> list[Message]:
    """事件日志 → 发给模型的消息。纯函数：同样的事件永远得到同样的消息；
    日志只追加，所以消息也只追加（压缩除外），每一步的请求开头都和上一步一样"""
    start = events[0]
    context = start.get("context", "")
    base, summary = 1, None
    for i, e in enumerate(events):
        if e["type"] == "compaction":
            base, summary = int(e["upto"]), e["summary"]

    messages: list[Message] = [{"role": "system", "content": system}]
    pending_context: str | None = context
    if summary is not None:
        head = f"{context}\n\n" if context else ""
        messages.append({"role": "user", "content": f"{head}<之前的进展>\n{summary}\n</之前的进展>"})
        pending_context = None

    for e in events[base:]:
        kind = e["type"]
        if kind == "user":
            text = e["text"]
            if pending_context:
                text = f"{pending_context}\n\n{text}"
            pending_context = None
            messages.append({"role": "user", "content": text})
        elif kind == "assistant":
            msg: Message = {"role": "assistant", "content": e.get("text", "")}
            if e.get("reasoning"):
                msg["reasoning_content"] = e["reasoning"]
            if e.get("tool_calls"):
                msg["tool_calls"] = [
                    {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}}
                    for c in e["tool_calls"]
                ]
            messages.append(msg)
        elif kind == "tool":
            messages.append({"role": "tool", "tool_call_id": e["id"], "content": e["content"]})
    return messages


class SessionStore:
    """data/sessions 下的会话文件；加载过的会话留在内存里（运行时状态挂在上面）"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._open: dict[str, Session] = {}

    def create(self, cwd: Path, mode: str, model: str, context: str, extra: dict[str, Any] | None = None) -> Session:
        self.root.mkdir(parents=True, exist_ok=True)
        sid = f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"
        path = self.root / f"{sid}.jsonl"
        event: Event = {
            "type": "start",
            "id": sid,
            "cwd": str(cwd),
            "mode": mode,
            "model": model,
            "context": context,
            "created": datetime.now().astimezone().isoformat(timespec="seconds"),
            **(extra or {}),
            "seq": 0,
            "t": round(time.time(), 3),
        }
        path.write_text(json.dumps(event, ensure_ascii=False) + "\n", encoding="utf-8")
        session = Session(path, [event])
        self._open[sid] = session
        return session

    def get(self, sid: str) -> Session | None:
        if not SESSION_ID.match(sid):
            return None
        if sid in self._open:
            return self._open[sid]
        path = self.root / f"{sid}.jsonl"
        if not path.exists():
            return None
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not events or events[0].get("type") != "start":
            return None
        session = Session(path, events)
        self._open[sid] = session
        return session

    def delete(self, sid: str) -> bool:
        """运行时清理进程和分支后，移除日志及已加载的对象。"""
        session = self.get(sid)
        if session is None:
            return False
        session.path.unlink(missing_ok=True)
        self._open.pop(sid, None)
        return True

    def list(self, limit: int = 50, query: str | None = None, cwd: str | None = None) -> list[dict[str, Any]]:
        if not self.root.exists() or limit <= 0:
            return []
        files = sorted(self.root.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        key = (query or "").strip().casefold()
        out = []
        for f in files:
            s = self.get(f.stem)
            if s is None or (cwd is not None and not same_project(s.cwd, cwd)):
                continue
            if key and key not in s.title.casefold() and key not in str(s.cwd).casefold():
                continue
            out.append(s.summary())
            if len(out) >= limit:
                break
        return out


def same_project(a: str | Path, b: str | Path) -> bool:
    """项目目录按路径比较，忽略大小写、斜杠方向和末尾斜杠。"""
    left = posixpath.normpath(str(a).replace("\\", "/")).casefold()
    right = posixpath.normpath(str(b).replace("\\", "/")).casefold()
    return left == right
