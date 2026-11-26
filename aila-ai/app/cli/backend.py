"""终端版连到哪：本地 AI 服务开着就走它的 HTTP 接口（桌面上也能同时看到这个会话），没开就在终端进程里自己跑运行时。
两种方式对上层完全一样。"""

import json
import os
from collections.abc import AsyncIterator
from dataclasses import asdict
from pathlib import Path
from typing import Any, Protocol

import httpx

from ..computer import computer_shell
from ..runtime.mcp import McpError
from ..runtime.memory import MemoryError_
from ..runtime.runner import Runtime, RuntimeError_

Event = dict[str, Any]


class BackendError(Exception):
    """说给用户听的错误"""


class Backend(Protocol):
    label: str

    async def create(self, cwd: str | None, mode: str, isolate: bool = False, budget: float | None = None,
                     *, kind: str = "project") -> dict[str, Any]: ...
    async def merge(self, sid: str, run_tests: bool = True) -> Event: ...
    async def context(self, sid: str) -> dict[str, Any]: ...
    async def discard(self, sid: str) -> Event: ...
    async def rename(self, sid: str, title: str) -> dict[str, Any]: ...
    async def delete(self, sid: str) -> None: ...
    async def search_sessions(self, query: str, cwd: str) -> list[dict[str, Any]]: ...
    async def sessions(self) -> list[dict[str, Any]]: ...
    async def session(self, sid: str) -> dict[str, Any]: ...
    def run(self, sid: str, text: str, review: bool = False, plan: bool = False) -> AsyncIterator[Event]: ...
    def plan_approve(self, sid: str, pause_each: bool = False) -> AsyncIterator[Event]: ...
    def plan_continue(self, sid: str) -> AsyncIterator[Event]: ...
    async def plan_reject(self, sid: str) -> Event: ...
    async def interrupt(self, sid: str) -> None: ...
    async def set_mode(self, sid: str, mode: str) -> None: ...
    async def compact(self, sid: str) -> None: ...
    async def answer(self, confirm_id: str, approved: bool, remember: bool = False) -> bool: ...
    async def close(self) -> None: ...
    async def rules(self) -> list[dict[str, Any]]: ...
    async def forget_rule(self, project: str, command: str) -> None: ...
    async def undo(self, sid: str, turn_seq: int) -> Event: ...
    async def set_budget(self, sid: str, amount: float | None) -> Event: ...
    async def commands(self, cwd: str | None, kind: str = "project") -> dict[str, Any]: ...
    async def memory(self, cwd: str) -> list[dict[str, Any]]: ...
    async def remember(self, cwd: str, text: str) -> dict[str, Any]: ...
    async def forget_memory(self, cwd: str, note_id: str) -> None: ...
    async def mcp_status(self) -> list[dict[str, Any]]: ...
    async def mcp_restart(self, name: str) -> list[dict[str, Any]]: ...


class EmbeddedBackend:
    """在终端进程里跑（AI 服务没开时）"""

    label = "内置"

    def __init__(self, runtime: Runtime) -> None:
        self.rt = runtime

    async def create(self, cwd: str | None, mode: str, isolate: bool = False, budget: float | None = None,
                     *, kind: str = "project") -> dict[str, Any]:
        try:
            session = self.rt.create(cwd, mode, isolate, budget=budget, kind=kind)  # type: ignore[arg-type]
        except RuntimeError_ as err:
            raise BackendError(str(err)) from err
        shell = computer_shell() if session.kind == "computer" else self.rt.shell
        return {**session.summary(), "shell": shell.label}

    async def merge(self, sid: str, run_tests: bool = True) -> Event:
        try:
            return await self.rt.merge(sid, run_tests)
        except RuntimeError_ as err:
            raise BackendError(str(err)) from err

    async def context(self, sid: str) -> dict[str, Any]:
        try:
            return self.rt.context(sid)
        except RuntimeError_ as err:
            raise BackendError(str(err)) from err

    async def discard(self, sid: str) -> Event:
        try:
            return await self.rt.discard(sid)
        except RuntimeError_ as err:
            raise BackendError(str(err)) from err

    async def sessions(self) -> list[dict[str, Any]]:
        return self.rt.store.list()

    async def rename(self, sid: str, title: str) -> dict[str, Any]:
        try:
            return self.rt.rename(sid, title).summary()
        except RuntimeError_ as err:
            raise BackendError(str(err)) from err

    async def search_sessions(self, query: str, cwd: str) -> list[dict[str, Any]]:
        return self.rt.store.list(query=query, cwd=cwd)

    async def delete(self, sid: str) -> None:
        try:
            await self.rt.delete(sid)
        except RuntimeError_ as err:
            raise BackendError(str(err)) from err

    async def session(self, sid: str) -> dict[str, Any]:
        try:
            session = self.rt.agent(sid).session
        except RuntimeError_ as err:
            raise BackendError(str(err)) from err
        return {**session.summary(), "running": self.rt.running(sid), "shell": self.rt.agent(sid).tools.shell.label, "events": session.events}

    async def run(self, sid: str, text: str, review: bool = False, plan: bool = False) -> AsyncIterator[Event]:
        try:
            stream = self.rt.run(sid, text, review=review, plan=plan)
        except RuntimeError_ as err:
            raise BackendError(str(err)) from err
        async for event in stream:
            yield event

    async def plan_approve(self, sid: str, pause_each: bool = False) -> AsyncIterator[Event]:
        try:
            decision = self.rt.decide_plan(sid, True, pause_each)
            stream = self.rt.drive_plan(sid, after=decision["seq"] - 1)
        except RuntimeError_ as err:
            raise BackendError(str(err)) from err
        async for event in stream:
            yield event

    async def plan_continue(self, sid: str) -> AsyncIterator[Event]:
        try:
            stream = self.rt.drive_plan(sid)
        except RuntimeError_ as err:
            raise BackendError(str(err)) from err
        async for event in stream:
            yield event

    async def plan_reject(self, sid: str) -> Event:
        try:
            return self.rt.decide_plan(sid, False)
        except RuntimeError_ as err:
            raise BackendError(str(err)) from err

    async def interrupt(self, sid: str) -> None:
        self.rt.interrupt(sid)

    async def set_mode(self, sid: str, mode: str) -> None:
        try:
            self.rt.set_mode(sid, mode)  # type: ignore[arg-type]
        except RuntimeError_ as err:
            raise BackendError(str(err)) from err

    async def compact(self, sid: str) -> None:
        try:
            await self.rt.compact(sid)
        except RuntimeError_ as err:
            raise BackendError(str(err)) from err

    async def answer(self, confirm_id: str, approved: bool, remember: bool = False) -> bool:
        return self.rt.confirms.answer(confirm_id, approved, remember)

    async def close(self) -> None:
        await self.rt.shutdown()

    async def rules(self) -> list[dict[str, Any]]:
        groups = {**self.rt.rules.all(), **self.rt.computer_rules.all()}
        return [{"project": project, "commands": commands} for project, commands in sorted(groups.items())]

    async def forget_rule(self, project: str, command: str) -> None:
        rules = self.rt.computer_rules if project == "computer" else self.rt.rules
        if command not in rules.commands(Path(project)):
            raise BackendError("没有这条记住的命令，可能已经删掉了。")
        rules.forget(Path(project), command)

    async def commands(self, cwd: str | None, kind: str = "project") -> dict[str, Any]:
        found = self.rt.commands(cwd, kind)
        return {"commands": sorted((c.summary() for c in found.values()), key=lambda c: c["name"]),
                "project_dir": str(Path(cwd) / ".aila" / "commands") if cwd and kind != "computer" else None,
                "user_dir": str(self.rt.data_dir / "commands")}

    async def memory(self, cwd: str) -> list[dict[str, Any]]:
        return [asdict(n) for n in self.rt.memory.notes(cwd)]

    async def remember(self, cwd: str, text: str) -> dict[str, Any]:
        try:
            note, added = self.rt.memory.add(cwd, text, "user")
        except MemoryError_ as err:
            raise BackendError(str(err)) from err
        return {**asdict(note), "added": added}

    async def forget_memory(self, cwd: str, note_id: str) -> None:
        if not self.rt.memory.remove(cwd, note_id):
            raise BackendError("没有这条记忆，可能已经删掉了。")

    async def mcp_status(self) -> list[dict[str, Any]]:
        await self.rt.mcp.wait_ready()
        return self.rt.mcp.status()

    async def mcp_restart(self, name: str) -> list[dict[str, Any]]:
        try:
            await self.rt.mcp.restart(name)
        except McpError as err:
            raise BackendError(str(err)) from err
        return self.rt.mcp.status()

    async def undo(self, sid: str, turn_seq: int) -> Event:
        try:
            return self.rt.undo(sid, turn_seq)
        except RuntimeError_ as err:
            raise BackendError(str(err)) from err

    async def set_budget(self, sid: str, amount: float | None) -> Event:
        try:
            return self.rt.set_budget(sid, amount)
        except RuntimeError_ as err:
            raise BackendError(str(err)) from err


class HttpBackend:
    """走本地 AI 服务（桌面端用的也是它）"""

    label = "本地服务"

    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = base_url
        # trust_env=False：本机的服务不走代理（开着代理软件时，环境变量里的代理会把 127.0.0.1 的请求也转走）
        self.client = httpx.AsyncClient(
            base_url=base_url, timeout=httpx.Timeout(30.0, read=None), transport=transport, trust_env=False
        )
        self.shell = ""

    async def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            res = await self.client.request(method, path, **kwargs)
        except httpx.HTTPError as err:
            raise BackendError(f"连不上 AI 服务（{self.base_url}）：{err}") from err
        if res.status_code >= 400:
            raise BackendError(_detail(res))
        return res.json()

    async def create(self, cwd: str | None, mode: str, isolate: bool = False, budget: float | None = None,
                     *, kind: str = "project") -> dict[str, Any]:
        return await self._call("POST", "/agent/sessions", json={"cwd": cwd, "mode": mode, "isolate": isolate,
                                                                 "budget": budget, "kind": kind})

    async def merge(self, sid: str, run_tests: bool = True) -> Event:
        return await self._call("POST", f"/agent/sessions/{sid}/merge", json={"run_tests": run_tests}, timeout=660)

    async def context(self, sid: str) -> dict[str, Any]:
        return await self._call("GET", f"/agent/sessions/{sid}/context")

    async def discard(self, sid: str) -> Event:
        return await self._call("POST", f"/agent/sessions/{sid}/discard", timeout=60)

    async def sessions(self) -> list[dict[str, Any]]:
        return await self._call("GET", "/agent/sessions")

    async def rename(self, sid: str, title: str) -> dict[str, Any]:
        return await self._call("PATCH", f"/agent/sessions/{sid}", json={"title": title})

    async def search_sessions(self, query: str, cwd: str) -> list[dict[str, Any]]:
        return await self._call("GET", "/agent/sessions", params={"q": query, "cwd": cwd})

    async def delete(self, sid: str) -> None:
        await self._call("DELETE", f"/agent/sessions/{sid}", timeout=60)

    async def session(self, sid: str) -> dict[str, Any]:
        return await self._call("GET", f"/agent/sessions/{sid}")

    async def run(self, sid: str, text: str, review: bool = False, plan: bool = False) -> AsyncIterator[Event]:
        body: dict[str, Any] = {"text": text}
        if review:
            body["review"] = True
        if plan:
            body["plan"] = True
        async for event in self._stream(f"/agent/sessions/{sid}/messages", body):
            yield event

    def plan_approve(self, sid: str, pause_each: bool = False) -> AsyncIterator[Event]:
        return self._stream(f"/agent/sessions/{sid}/plan/approve", {"pause_each": pause_each})

    def plan_continue(self, sid: str) -> AsyncIterator[Event]:
        return self._stream(f"/agent/sessions/{sid}/plan/continue", {})

    async def plan_reject(self, sid: str) -> Event:
        return await self._call("POST", f"/agent/sessions/{sid}/plan/reject")

    async def _stream(self, path: str, body: dict[str, Any]) -> AsyncIterator[Event]:
        try:
            async with self.client.stream("POST", path, json=body) as res:
                if res.status_code >= 400:
                    await res.aread()
                    raise BackendError(_detail(res))
                async for line in res.aiter_lines():
                    if line.startswith("data:"):
                        yield json.loads(line[5:])
        except httpx.HTTPError as err:
            raise BackendError(f"和 AI 服务的连接断了：{err}") from err

    async def interrupt(self, sid: str) -> None:
        await self._call("POST", f"/agent/sessions/{sid}/interrupt")

    async def set_mode(self, sid: str, mode: str) -> None:
        await self._call("PUT", f"/agent/sessions/{sid}/mode", json={"mode": mode})

    async def compact(self, sid: str) -> None:
        await self._call("POST", f"/agent/sessions/{sid}/compact")

    async def answer(self, confirm_id: str, approved: bool, remember: bool = False) -> bool:
        try:
            await self._call("POST", "/agent/confirm", json={"id": confirm_id, "approved": approved, "remember": remember})
        except BackendError:
            return False
        return True

    async def close(self) -> None:
        await self.client.aclose()

    async def rules(self) -> list[dict[str, Any]]:
        return await self._call("GET", "/agent/rules")

    async def forget_rule(self, project: str, command: str) -> None:
        await self._call("DELETE", "/agent/rules", json={"project": project, "command": command})

    async def commands(self, cwd: str | None, kind: str = "project") -> dict[str, Any]:
        return await self._call("GET", "/agent/commands", params={"kind": kind, **({"cwd": cwd} if cwd else {})})

    async def memory(self, cwd: str) -> list[dict[str, Any]]:
        return await self._call("GET", "/agent/memory", params={"cwd": cwd})

    async def remember(self, cwd: str, text: str) -> dict[str, Any]:
        return await self._call("POST", "/agent/memory", json={"cwd": cwd, "text": text})

    async def forget_memory(self, cwd: str, note_id: str) -> None:
        await self._call("DELETE", "/agent/memory", json={"cwd": cwd, "id": note_id})

    async def mcp_status(self) -> list[dict[str, Any]]:
        return await self._call("GET", "/agent/mcp")

    async def mcp_restart(self, name: str) -> list[dict[str, Any]]:
        return await self._call("POST", f"/agent/mcp/{name}/restart", timeout=90)

    async def undo(self, sid: str, turn_seq: int) -> Event:
        return await self._call("POST", f"/agent/sessions/{sid}/undo", json={"turn_seq": turn_seq}, timeout=60)

    async def set_budget(self, sid: str, amount: float | None) -> Event:
        return await self._call("PUT", f"/agent/sessions/{sid}/budget", json={"amount": amount})


def _detail(res: httpx.Response) -> str:
    try:
        detail = res.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    return str(detail) if detail else f"AI 服务返回 {res.status_code}"


def service_url() -> str:
    from ..deps import settings

    return os.getenv("AILA_AI_URL") or f"http://{settings.host}:{settings.port}"


async def connect(prefer: str = "auto") -> Backend:
    """prefer：auto（服务开着就用服务）、service（一定要用服务）、local（不用服务）"""
    if prefer != "local":
        url = service_url()
        backend = HttpBackend(url)
        try:
            res = await backend.client.get("/agent/sessions", timeout=1.5)
            if res.status_code == 200:
                return backend
        except httpx.HTTPError:
            pass
        await backend.close()
        if prefer == "service":
            raise BackendError(f"AI 服务没开（{url}）。先运行 .venv\\Scripts\\python -m app，或者去掉 --service。")
    return EmbeddedBackend(_local_runtime())


def _local_runtime() -> Runtime:
    from ..deps import get_code_projects, get_coder_fast_model, get_coder_model, get_knowledge, get_reranker, settings
    from ..runtime.loop import AgentConfig

    config = AgentConfig(max_steps=settings.coder_max_steps, context_window=settings.coder_context)
    return Runtime(settings.data_dir, get_coder_model, config=config, code_projects=get_code_projects, knowledge=get_knowledge,
                   fast_factory=get_coder_fast_model, computer_inventory=settings.computer_inventory, reranker=get_reranker)
