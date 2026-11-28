"""Agent 循环。

一轮（turn）= 用户说一句话以后，她一直做到不再调用工具为止；
一步（step）= 一次模型请求，加上它要求的工具调用。流程：

  turn_start → [pre_step → 请求模型（流式：文字、思考、工具调用、用量）→ post_step
               → 每个工具：权限判断 →（要问就问用户）→ pre_tool → 执行 → post_tool] × N → turn_end

钩子（Hooks）挂在这些点上：以后桌面上的她做动作、星图标出改过的文件、技能包扩展，都挂在这里，不改循环本身。

每一步发给模型的消息都由会话日志推出来（只追加），开头和上一步一样，前缀缓存能命中。
上下文快满时先压缩：请求和平时一模一样（同样的工具列表），只在最后加一句「请总结」，所以压缩这一次请求也几乎全部命中缓存。

用户拒绝（或者一直没回答）一个操作时，这一轮立刻结束，等用户说怎么办——不让她换个办法绕过去。
被打断时补齐日志：没执行的工具调用记成「被打断」，说了一半的话也记下来，下次请求的消息仍然是完整、合法的。
"""

import asyncio
import inspect
import time
import json
import logging
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

from ..confirm import Confirmations
from ..llm import CoderModel, Reasoning, ToolCall, Usage, classify_error
from .permissions import Policy
from .prompt import SYSTEM_PROMPT
from .session import Event, Session
from .tools import PROGRESS, CodingTool, CodingTools, Outcome
from .workspace import WorkspaceError

log = logging.getLogger("aila.runtime")

COMPACT_PROMPT = (
    "上下文快满了。请把到目前为止的工作写成一份交接说明，之后会用它代替前面的全部对话继续工作。包括：\n"
    "1. 用户要做的任务（原话的要点）\n2. 已经做了什么：改了哪些文件、为什么\n"
    "3. 现在的状态：测试或构建的结果\n4. 还没做的事\n5. 重要的发现：文件路径、函数名、命令、踩过的坑\n"
    "只写交接说明，不要调用工具。"
)
NOT_DONE = {
    "rejected": "用户没有同意，这个操作没有执行。先停下来，等用户说接下来怎么做。",
    "timeout": "用户一直没有回答，这个操作没有执行。先停下来，等用户回来。",
}
SKIPPED = "前面的操作没有被同意，这个也没有执行。"
# 一轮里连着几次被权限挡下（deny，不是你点了「不要」）就停下：以前只读模式下她会换着法子一直试，
# 看起来像一直在想；停下来以后任务页写清原因，你换个模式或者说清楚再接着做
MAX_DENIED = 3
INTERRUPTED = "被用户打断了，没有执行。"
# 一步里连着的可以一起跑的调用（子 Agent），最多同时跑几个
MAX_PARALLEL = 4
# 一直在网页上找：最近 BROWSER_WINDOW 步里有 BROWSER_NUDGE 步在用浏览器，就在工具结果后面提醒她换个办法
# （以前她看不到 iframe 里的东西，来回开网页、写临时检查页，把步数用光了）
BROWSER_WINDOW = 12
BROWSER_NUDGE = 8
BROWSER_HINT = "\n\n（最近 {total} 步里有 {n} 步在用浏览器。要找的东西网页上一直看不到的话，换个办法：读源码、search 搜、跑命令或脚本、browser_screenshot 看图。）"


class BrowserStreak:
    """记着最近用过的工具：一直在用浏览器找东西时，给这个工具结果后面接一句提醒；提醒过以后隔一个窗口才再提醒"""

    def __init__(self) -> None:
        self.recent: deque[str] = deque(maxlen=BROWSER_WINDOW)
        self.since_hint = BROWSER_WINDOW

    def note(self, name: str) -> str:
        self.recent.append(name)
        self.since_hint += 1
        if not name.startswith("browser_") or self.since_hint < BROWSER_WINDOW:
            return ""
        n = sum(1 for t in self.recent if t.startswith("browser_"))
        if n < BROWSER_NUDGE:
            return ""
        self.since_hint = 0
        return BROWSER_HINT.format(total=len(self.recent), n=n)
DEFAULT_CONTEXT_WINDOW = 128_000


@dataclass
class AgentConfig:
    max_steps: int = 60
    max_tokens: int = 8192
    context_window: int | None = None
    compact_at: float = 0.8


@dataclass
class Hooks:
    handlers: dict[str, list[Callable[..., Any]]] = field(default_factory=dict)

    def on(self, name: str, fn: Callable[..., Any]) -> None:
        self.handlers.setdefault(name, []).append(fn)

    async def emit(self, name: str, **payload: Any) -> None:
        for fn in self.handlers.get(name, []):
            try:
                result = fn(**payload)
                if inspect.isawaitable(result):
                    await result
            except Exception:  # 钩子出错不能拖垮任务
                log.exception("钩子 %s 出错", name)


class Agent:
    def __init__(
        self,
        session: Session,
        model: CoderModel,
        tools: CodingTools,
        policy: Policy,
        confirms: Confirmations | None,
        config: AgentConfig | None = None,
        hooks: Hooks | None = None,
        system: str = SYSTEM_PROMPT,
    ) -> None:
        self.session = session
        self.model = model
        self.tools = tools
        self.policy = policy
        self.confirms = confirms
        self.config = config or AgentConfig()
        self.hooks = hooks or Hooks()
        # 被权限挡下时接在原因后面的一句提示（runner 在出方案、/review 那一轮设，结束后清掉）
        self.deny_hint = ""
        self.system = system
        # 这一步说到一半的话（被打断时记下来）
        self._partial = ""
        # 看是不是一直在网页上找
        self._browser = BrowserStreak()

    # ---------- 一轮 ----------

    async def turn(self, text: str, extra: dict[str, Any] | None = None) -> AsyncIterator[Event]:
        # extra：界面用的标记（比如 /review 的 review、focus），不进模型消息
        yield self.session.append({"type": "user", "text": text, "model": self.model.name, **(extra or {})})
        await self.hooks.emit("turn_start", session=self.session, text=text)
        reason, error = "done", ""
        denied = 0  # 连着被权限挡下几次（中间有一次做成了就清零）
        try:
            for step in range(1, self.config.max_steps + 1):
                if self._budget_exhausted():
                    reason = "budget"
                    break
                if self._should_compact():
                    async for e in self.compact():
                        yield e
                    if self._budget_exhausted():
                        reason = "budget"
                        break
                calls: list[ToolCall] = []
                async for e in self._step(step, calls):
                    yield e
                if not calls:
                    break
                stop = ""
                i = 0
                while i < len(calls):
                    if stop:
                        yield self._tool_event(calls[i], Outcome(SKIPPED, ok=False), status="skipped")
                        i += 1
                        continue
                    batch = self._parallel_batch(calls, i)
                    if len(batch) > 1:
                        async for e in self._run_parallel(batch):
                            yield e
                            denied = self._count_denied(e, denied)
                        i += len(batch)
                        continue
                    async for e in self._run_tool(calls[i]):
                        yield e
                        denied = self._count_denied(e, denied)
                        if e.get("type") == "confirm_end" and e["result"] != "approved":
                            stop = e["result"]
                    i += 1
                if stop:
                    reason = stop
                    break
                if denied >= MAX_DENIED:
                    # 权限一直挡着（比如只读模式下想改文件）：再试下去只会原地打转，停下来说明白
                    reason = "denied"
                    break
            else:
                reason = "max_steps"
        except asyncio.CancelledError:
            self._close("interrupted")
            raise
        except Exception as err:
            code, message = classify_error(err)
            log.exception("编程 Agent 出错")
            reason, error = "error", message if code != "unknown" else f"{message}（{type(err).__name__}）"
            for e in self._close("error", error, code):
                yield e
            return
        end = self.session.append({"type": "turn_end", "reason": reason, "cost": self.session.turn_cost})
        await self.hooks.emit("turn_end", session=self.session, reason=reason)
        yield end

    async def _step(self, step: int, calls: list[ToolCall]) -> AsyncIterator[Event]:
        await self.hooks.emit("pre_step", session=self.session, step=step)
        messages = self.session.messages(self.system)
        text, thought = "", ""
        usage: Usage | None = None
        self._partial = ""
        # 执行轨迹：这一步等了多久才收到第一个字（模型在想）、一共多久
        t0 = time.perf_counter()
        first: float | None = None
        async for item in self.model.step(messages, self.tools.specs, self.config.max_tokens):
            if first is None:
                first = time.perf_counter() - t0
            if isinstance(item, ToolCall):
                calls.append(item)
            elif isinstance(item, Usage):
                usage = item
            elif isinstance(item, Reasoning):
                thought += item.text
                yield {"type": "reasoning_delta", "text": item.text}
            else:
                text += item
                self._partial = text
                yield {"type": "text_delta", "text": item}
        self._partial = ""
        yield self.session.append({
            "type": "assistant",
            "text": text,
            **({"reasoning": thought} if thought else {}),
            "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in calls],
            "timing": {"first": round(first or 0.0, 3), "total": round(time.perf_counter() - t0, 3)},
        })
        if usage:
            yield self.session.append({"type": "usage", "model": self.model.name, "step": step, **asdict(usage)})
        await self.hooks.emit("post_step", session=self.session, step=step, usage=usage)

    # ---------- 工具 ----------

    async def _run_tool(self, call: ToolCall) -> AsyncIterator[Event]:
        tool = self.tools.tools.get(call.name)
        args = _parse(call.arguments)
        if tool is None:
            yield self._tool_event(call, Outcome(f"没有叫 {call.name} 的工具。", ok=False))
            return
        if args is None:
            yield self._tool_event(call, Outcome("工具参数不是合法的 JSON，请重新调用。", ok=False))
            return
        kind = tool.kind(args)
        # 按命令原文判断的是命令；MCP 工具按工具名判断「以后都不用问」
        command = str(args.get("command", "")) if kind == "command" else call.name if kind == "mcp" else ""
        if tool.permission_key is not None:
            command = tool.permission_key(args)
        decision = self.policy.decide(kind, command)

        if decision.verdict == "deny":
            # deny_hint：这一轮为什么只读、该怎么做（出方案那一轮：别换着法子试，把要改的写进计划交上来）
            reason = decision.reason + (f"\n{self.deny_hint}" if self.deny_hint else "")
            yield self._tool_event(call, Outcome(reason, ok=False), status="denied")
            return
        if decision.verdict == "ask":
            if self.confirms is None:
                yield self._tool_event(call, Outcome("这个操作需要用户确认，但现在没法问用户，没有执行。", ok=False), status="denied")
                return
            pending = self.confirms.open()
            try:
                yield self.session.append({
                    "type": "confirm",
                    "id": pending.id,
                    "call_id": call.id,
                    "tool": call.name,
                    "kind": kind,
                    "action": tool.status(args),
                    "detail": tool.preview(args) if tool.preview else json.dumps(args, ensure_ascii=False)[:2000],
                    "reason": decision.reason,
                    "can_remember": decision.can_remember,
                })
                result = await self.confirms.wait(pending)
            finally:
                self.confirms.close(pending)
            remember = pending.remember and decision.can_remember
            yield self.session.append({"type": "confirm_end", "id": pending.id, "result": result, "remember": remember})
            if result != "approved":
                yield self._tool_event(call, Outcome(NOT_DONE[result], ok=False), status=result)
                return
            if self.session.kind == "computer":
                # 等用户点确认时可能已经换成只读，或文件目标变成了受保护位置。
                try:
                    current_kind = tool.kind(args)
                except WorkspaceError as err:
                    yield self._tool_event(call, Outcome(str(err), ok=False), status="denied")
                    return
                current = self.policy.decide(current_kind, command)
                if current.verdict == "deny":
                    yield self._tool_event(call, Outcome(current.reason, ok=False), status="denied")
                    return
                if current_kind != kind or (current.verdict == "ask" and current != decision):
                    yield self._tool_event(call, Outcome("确认期间操作范围变了，重新调用工具核对后再做。", ok=False), status="denied")
                    return
            if remember:
                self.policy.remember(kind, command)

        await self.hooks.emit("pre_tool", session=self.session, call=call, args=args)
        yield {"type": "tool_start", "id": call.id, "name": call.name, "status": tool.status(args)}
        progress: asyncio.Queue[Event] = asyncio.Queue()
        job = self._start(call, tool, args, progress)
        try:
            async for e in self._drain(job, progress):
                yield e
            outcome = job.result()
        finally:
            job.cancel()  # 被打断：工具一起停
        for e in await self._finish_tool(call, args, outcome):
            yield e

    def _start(self, call: ToolCall, tool: CodingTool, args: dict[str, Any],
               progress: "asyncio.Queue[Event]") -> "asyncio.Task[Outcome]":
        """开始跑一个工具：它报的进度进 progress 队列（任务创建时带走当时的上下文，所以先设好再创建）"""
        token = PROGRESS.set(lambda text, who: progress.put_nowait({"type": "tool_progress", "id": call.id, "who": who, "text": text}))
        try:
            return asyncio.ensure_future(self._execute(call, tool, args))
        finally:
            PROGRESS.reset(token)

    @staticmethod
    async def _drain(job: "asyncio.Task[Outcome]", progress: "asyncio.Queue[Event]") -> AsyncIterator[Event]:
        """等 job 跑完，期间把报上来的进度转出去（只推给界面，不写日志）"""
        while not job.done():
            getter = asyncio.ensure_future(progress.get())
            try:
                done, _ = await asyncio.wait({job, getter}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                if not getter.done():
                    getter.cancel()
            if getter in done:
                yield getter.result()
        while not progress.empty():
            yield progress.get_nowait()

    def _parallel_batch(self, calls: list[ToolCall], i: int) -> list[ToolCall]:
        """从第 i 个开始，连着的、可以一起跑、而且不用问用户的调用（最多 MAX_PARALLEL 个）"""
        batch: list[ToolCall] = []
        for call in calls[i : i + MAX_PARALLEL]:
            tool = self.tools.tools.get(call.name)
            args = _parse(call.arguments)
            if tool is None or not tool.parallel or args is None:
                break
            if self.policy.decide(tool.kind(args)).verdict != "allow":
                break
            batch.append(call)
        return batch

    async def _run_parallel(self, batch: list[ToolCall]) -> AsyncIterator[Event]:
        """一起跑：先都开始，再按调用的顺序记结果（日志里的顺序和模型要求的一样，前缀稳定）"""
        jobs = []
        progress: asyncio.Queue[Event] = asyncio.Queue()
        for call in batch:
            tool, args = self.tools.tools[call.name], _parse(call.arguments) or {}
            await self.hooks.emit("pre_tool", session=self.session, call=call, args=args)
            yield {"type": "tool_start", "id": call.id, "name": call.name, "status": tool.status(args)}
            jobs.append((call, args, self._start(call, tool, args, progress)))
        try:
            for call, args, job in jobs:
                async for e in self._drain(job, progress):  # 等这一个的时候，别的报的进度也转出去
                    yield e
                outcome = job.result()
                for e in await self._finish_tool(call, args, outcome):
                    yield e
        finally:
            for _, _, job in jobs:
                job.cancel()  # 被打断：还没跑完的一起停

    async def _execute(self, call: ToolCall, tool: CodingTool, args: dict[str, Any]) -> Outcome:
        t0 = time.perf_counter()
        try:
            outcome = await tool.run(args)
        except asyncio.CancelledError:
            raise
        except WorkspaceError as err:
            outcome = Outcome(str(err), ok=False)
        except Exception as err:  # 工具自己的 bug 不能拖垮任务
            log.exception("工具 %s 出错", call.name)
            outcome = Outcome(f"工具出错了（{type(err).__name__}：{err}）", ok=False)
        outcome.extra["seconds"] = round(time.perf_counter() - t0, 3)  # 执行轨迹用
        return outcome

    async def _finish_tool(self, call: ToolCall, args: dict[str, Any], outcome: Outcome) -> list[Event]:
        events = [self._tool_event(call, outcome)]
        if outcome.usage:
            # 工具里的子 Agent 用的量：purpose 标出来，算总账时算进去，但不算主 Agent 的一步
            events.append(self.session.append({
                "type": "usage", "model": str(outcome.extra.get("model", "")), "step": 0, "purpose": call.name,
                **asdict(outcome.usage),
            }))
        await self.hooks.emit("post_tool", session=self.session, call=call, args=args, outcome=outcome)
        return events

    @staticmethod
    def _count_denied(event: Event, denied: int) -> int:
        """工具结果是被权限挡下的就加一，做成了就清零，别的事件不变"""
        if event.get("type") != "tool":
            return denied
        if event.get("status") == "denied":
            return denied + 1
        return 0 if event.get("status") == "ok" else denied

    def _tool_event(self, call: ToolCall, outcome: Outcome, status: str | None = None) -> Event:
        return self.session.append({
            "type": "tool",
            "id": call.id,
            "name": call.name,
            "status": status or ("ok" if outcome.ok else "error"),
            "content": outcome.content + self._browser.note(call.name),
            **outcome.extra,
        })

    # ---------- 压缩 ----------

    def _budget_exhausted(self) -> bool:
        budget = self.session.budget
        return budget is not None and self.session.known_cost >= Decimal(str(budget))

    @property
    def context_window(self) -> int:
        """显式配置优先，否则按当前模型的窗口决定何时压缩。"""
        if self.config.context_window is not None:
            return self.config.context_window
        return getattr(self.model, "context_window", None) or DEFAULT_CONTEXT_WINDOW

    def _should_compact(self) -> bool:
        return self.session.last_prompt_tokens > self.context_window * self.config.compact_at

    async def compact(self) -> AsyncIterator[Event]:
        """用模型把前面的工作总结成交接说明，之后用它代替前面的全部消息"""
        messages = self.session.messages(self.system) + [{"role": "user", "content": COMPACT_PROMPT}]
        summary = ""
        usage: Usage | None = None
        async for item in self.model.step(messages, self.tools.specs, 4096):
            if isinstance(item, str):
                summary += item
            elif isinstance(item, Usage):
                usage = item
        if not summary.strip():
            return
        yield self.session.append({"type": "compaction", "summary": summary.strip(), "upto": len(self.session.events)})
        if usage:
            yield self.session.append({"type": "usage", "model": self.model.name, "step": 0, "purpose": "compaction", **asdict(usage)})

    # ---------- 收尾 ----------

    def repair(self) -> list[Event]:
        """上次没做完就断了的一轮：补完整，记成被打断"""
        return self._close("interrupted")

    def _close(self, reason: str, error: str = "", error_code: str = "") -> list[Event]:
        """出错或被打断：把日志补完整，下次请求的消息仍然合法（每个工具调用都有结果，用户的话后面有回复）"""
        first = len(self.session.events)
        if self._partial:
            self.session.append({"type": "assistant", "text": self._partial + "……（被打断）", "tool_calls": []})
            self._partial = ""
        for c in self.session.unanswered_calls():
            content = INTERRUPTED if reason == "interrupted" else f"出错了，没有执行：{error}"
            self.session.append({"type": "tool", "id": c["id"], "name": c["name"], "status": reason, "content": content})
        if self.session.last_message_role() == "user":
            note = "（这一轮被打断了，没有回复）" if reason == "interrupted" else f"（出错了，没有回复：{error}）"
            self.session.append({"type": "assistant", "text": note, "tool_calls": []})
        self.session.append({"type": "turn_end", "reason": reason, "cost": self.session.turn_cost,
                             **({"error": error} if error else {}),
                             **({"error_code": error_code} if error_code else {})})
        return self.session.events[first:]


def _parse(raw: str) -> dict[str, Any] | None:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None

