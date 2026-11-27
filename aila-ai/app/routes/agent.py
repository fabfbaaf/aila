"""编程 Agent 的接口（桌面工作窗口和终端都用这一套）。

  POST   /agent/sessions                   新建会话 {cwd, mode, isolate（在新分支上做）}
  POST   /agent/sessions/{id}/merge        在新分支上做时：跑测试、合并回你的代码 {run_tests}
  POST   /agent/sessions/{id}/discard      放弃分支（你的代码保持原样）
  POST   /agent/tasks                      新建会话并马上开始做 {text, cwd 或 project（代码库里的项目 id）, mode}
  GET    /agent/sessions                   会话列表（最近的在前）
  GET    /agent/sessions/{id}              会话的全部日志事件（界面据此重建）
  POST   /agent/sessions/{id}/messages     说一句话，开始一轮；返回这一轮的事件流（SSE）。连接断了她照样做完
                                            {text, review}：review=true 是 /review，只读地审没提交的改动，text 可以空着（要特别注意什么）
  GET    /agent/sessions/{id}/events       订阅：补发 after 之后的事件，正在做的话接着推实时事件（SSE）
  POST   /agent/sessions/{id}/interrupt    打断
  POST   /agent/sessions/{id}/plan/approve 批准她交的计划并开始按阶段做 {pause_each}；返回一直到计划停下的事件流（SSE）
  POST   /agent/sessions/{id}/plan/continue 计划停下以后接着做（SSE）
  POST   /agent/sessions/{id}/plan/reject  不要这份计划
  PUT    /agent/sessions/{id}/mode         换权限模式 {mode}
  POST   /agent/sessions/{id}/compact      手动压缩上下文
  GET    /agent/sessions/{id}/context      上下文账本：上下文由哪几块组成、各占多少
  POST   /agent/confirm                    回答她的确认 {id, approved, remember}
  GET    /agent/commands?cwd=&kind=        自定义命令：项目 .aila/commands/ 和自己的 commands/ 里的「/名字」
  GET    /agent/memory?cwd=                项目记忆：她在这个项目里记下的约定（下次开会话时带上）
  POST   /agent/memory                     加一条 {cwd, text}
  DELETE /agent/memory                     删一条 {cwd, id}
  GET    /agent/mcp                        MCP 外部工具服务：状态、工具、最近的日志（env 只给名字，不给值）
  POST   /agent/mcp/preview                贴进来的配置先看一眼 {json}：要启动的命令（不保存）
  POST   /agent/mcp/import                 保存贴进来的配置并启动 {json}
  POST   /agent/mcp/{name}/restart         重启一个服务
  PUT    /agent/mcp/{name}/disabled        停用 / 启用 {disabled}
  DELETE /agent/mcp/{name}                 删掉一个服务
  DELETE /agent/mcp/{name}/allow           撤销「这个工具以后都不用问」 {tool}

和其他接口一样，只接受本机页面和本机程序的请求（见 security.py）。
"""

import asyncio
import json
from dataclasses import asdict
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from ..computer import computer_shell
from ..deps import get_runtime
from ..runtime.localagents import LocalAgentError, detect, install_adapters
from ..runtime.permissions import MODE_LABELS
from ..runtime.pricing import validate_budget
from ..runtime.mcp import McpError, parse_import
from ..runtime.memory import MemoryError_
from ..runtime.runner import Runtime, RuntimeError_
from ..runtime.session import Event
from ..i18n import localize_event, localize_labels, localize_summary
from ..sse import with_heartbeat

router = APIRouter(prefix="/agent")

ModeName = Literal["read_only", "ask", "auto", "full"]


def _budget_input(value: object) -> float | None:
    # FastAPI 的默认校验响应会回显 input，NaN/Infinity 无法序列化成 JSON。
    try:
        return validate_budget(value)
    except (ValueError, TypeError) as err:
        raise HTTPException(422, str(err)) from err


class NewSession(BaseModel):
    cwd: str | None = Field(default=None, min_length=1, max_length=1000)
    kind: Literal["project", "computer"] = "project"
    mode: ModeName = "ask"
    isolate: bool = False  # 在新分支上做
    # 交给本机的 Codex / Claude Code 做（不填就是艾拉自己做）
    engine: Literal["codex", "claude"] | None = None
    budget: float | None = Field(default=None, ge=0, allow_inf_nan=False, strict=True)
    _valid_budget = field_validator("budget", mode="before")(_budget_input)


class Merge(BaseModel):
    run_tests: bool = True


class NewTask(BaseModel):
    """从别处交过来的任务（屏幕上的报错、星图、对话）：开一个会话，马上开始做"""

    text: str = Field(min_length=1, max_length=20000)
    cwd: str | None = Field(default=None, max_length=1000)
    project: str | None = Field(default=None, max_length=64)  # 代码库里登记的项目 id
    mode: ModeName = "ask"
    kind: Literal["project", "computer"] = "project"


class Say(BaseModel):
    text: str = Field(default="", max_length=20000)
    review: bool = False
    plan: bool = False  # 先出方案：这一轮只读，text 是目标

    @model_validator(mode="after")
    def _needs_text(self) -> "Say":
        if not self.review and not self.text:
            raise ValueError("要做什么？")
        return self


class PlanApprove(BaseModel):
    pause_each: bool = False  # 每个阶段做完停一下，等你看过再接着做


class SetMode(BaseModel):
    mode: ModeName


class Answer(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    approved: bool
    remember: bool = False


def _sse(events: AsyncIterator[Event]) -> StreamingResponse:
    async def body() -> AsyncIterator[str]:
        async for event in events:
            yield f"data: {json.dumps(localize_event(event), ensure_ascii=False)}\n\n"  # 英文界面时给人看的字段换成英文

    # 隔 15 秒没事件就发一行心跳：她跑长命令、想很久时连接不会被桌面端当成断了
    return StreamingResponse(with_heartbeat(body()), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


def _session_or_404(rt: Runtime, sid: str):  # noqa: ANN202
    try:
        return rt.agent(sid).session
    except RuntimeError_ as err:
        raise HTTPException(404, str(err)) from err


@router.post("/sessions", status_code=201)
async def create_session(req: NewSession, rt: Runtime = Depends(get_runtime)) -> dict:
    try:
        session = rt.create(req.cwd, req.mode, req.isolate, budget=req.budget, kind=req.kind, engine=req.engine)
    except RuntimeError_ as err:
        raise HTTPException(400, str(err)) from err
    modes = {k: v for k, v in MODE_LABELS.items() if session.kind != "computer" or k in ("read_only", "ask")}
    shell = computer_shell() if session.kind == "computer" else rt.shell
    return {**session.summary(), "shell": shell.label, "modes": modes}


@router.post("/tasks", status_code=201)
async def new_task(req: NewTask, rt: Runtime = Depends(get_runtime)) -> dict:
    try:
        if req.kind == "computer" and req.project:
            raise RuntimeError_("电脑任务不用选项目。")
        cwd = rt.project_dir(req.project) if req.project else req.cwd
        if req.kind == "project" and not cwd:
            raise RuntimeError_("要告诉我在哪个项目里做。")
        session = rt.create(cwd, req.mode, kind=req.kind)
        rt.start(session.id, req.text)
    except RuntimeError_ as err:
        raise HTTPException(400, str(err)) from err
    return {**session.summary(), "running": True}


@router.get("/sessions")
async def list_sessions(rt: Runtime = Depends(get_runtime), q: str | None = None, cwd: str | None = None) -> list[dict]:
    return [{**localize_summary(s), "running": rt.running(s["id"])} for s in rt.store.list(query=q, cwd=cwd)]


@router.get("/sessions/{sid}")
async def get_session(sid: str, rt: Runtime = Depends(get_runtime)) -> dict:
    session = _session_or_404(rt, sid)
    shell = computer_shell() if session.kind == "computer" else rt.shell
    return {**localize_summary(session.summary()), "running": rt.running(sid), "shell": shell.label,
            "events": [localize_event(e) for e in session.events]}  # 打开旧会话时进度、确认也按现在的语言显示


@router.post("/sessions/{sid}/messages")
async def say(sid: str, req: Say, rt: Runtime = Depends(get_runtime)) -> StreamingResponse:
    _session_or_404(rt, sid)
    try:
        stream = rt.run(sid, req.text, review=req.review, plan=req.plan)
    except RuntimeError_ as err:
        raise HTTPException(409, str(err)) from err
    return _sse(stream)


@router.get("/sessions/{sid}/events")
async def events(sid: str, after: int = -1, rt: Runtime = Depends(get_runtime)) -> StreamingResponse:
    _session_or_404(rt, sid)
    return _sse(rt.subscribe(sid, after))


@router.post("/sessions/{sid}/interrupt")
async def interrupt(sid: str, rt: Runtime = Depends(get_runtime)) -> dict:
    _session_or_404(rt, sid)
    return {"interrupted": rt.interrupt(sid)}


@router.put("/sessions/{sid}/mode")
async def set_mode(sid: str, req: SetMode, rt: Runtime = Depends(get_runtime)) -> dict:
    _session_or_404(rt, sid)
    try:
        rt.set_mode(sid, req.mode)
    except RuntimeError_ as err:
        raise HTTPException(400, str(err)) from err
    return {"mode": req.mode, "label": MODE_LABELS[req.mode]}


@router.get("/sessions/{sid}/context")
async def context(sid: str, rt: Runtime = Depends(get_runtime)) -> dict:
    """上下文账本：下一步发给模型的上下文由哪几块组成、各占多少、命中缓存多少"""
    _session_or_404(rt, sid)
    ledger = rt.context(sid)
    return {**ledger, "segments": localize_labels(ledger.get("segments", [])), "biggest": localize_labels(ledger.get("biggest", []))}


@router.post("/sessions/{sid}/compact")
async def compact(sid: str, rt: Runtime = Depends(get_runtime)) -> dict:
    _session_or_404(rt, sid)
    try:
        await rt.compact(sid)
    except RuntimeError_ as err:
        raise HTTPException(409, str(err)) from err
    return {"ok": True}


@router.post("/sessions/{sid}/merge")
async def merge(sid: str, req: Merge, rt: Runtime = Depends(get_runtime)) -> dict:
    _session_or_404(rt, sid)
    try:
        return await rt.merge(sid, req.run_tests)
    except RuntimeError_ as err:
        raise HTTPException(409, str(err)) from err


@router.post("/sessions/{sid}/discard")
async def discard(sid: str, rt: Runtime = Depends(get_runtime)) -> dict:
    _session_or_404(rt, sid)
    try:
        return await rt.discard(sid)
    except RuntimeError_ as err:
        raise HTTPException(409, str(err)) from err


@router.post("/confirm")
async def answer(req: Answer, rt: Runtime = Depends(get_runtime)) -> dict:
    if not rt.confirms.answer(req.id, req.approved, req.remember):
        raise HTTPException(404, "这个确认已经结束了")
    return {"ok": True}


class ForgetRule(BaseModel):
    project: str = Field(min_length=1, max_length=1000)
    command: str = Field(min_length=1, max_length=20000)


@router.get("/rules")
async def list_rules(rt: Runtime = Depends(get_runtime)) -> list[dict]:
    """按项目列出用户记住的命令，不执行其中任何内容。"""
    groups = {**rt.rules.all(), **rt.computer_rules.all()}
    return [{"project": project, "commands": commands} for project, commands in sorted(groups.items())]


@router.delete("/rules")
async def forget_rule(req: ForgetRule, rt: Runtime = Depends(get_runtime)) -> dict:
    rules = rt.computer_rules if req.project == "computer" else rt.rules
    if req.command not in rules.commands(req.project):  # type: ignore[arg-type]
        raise HTTPException(404, "没有这条记住的命令，可能已经删掉了。")
    rules.forget(req.project, req.command)  # type: ignore[arg-type]
    return {"ok": True}


class RenameSession(BaseModel):
    title: str = Field(min_length=1, max_length=100)


@router.patch("/sessions/{sid}")
async def rename_session(sid: str, req: RenameSession, rt: Runtime = Depends(get_runtime)) -> dict:
    if rt.store.get(sid) is None:
        raise HTTPException(404, "没有这个会话。")
    if not req.title.strip():
        raise HTTPException(422, "标题要有 1–100 个字。")
    try:
        return {**rt.rename(sid, req.title).summary(), "running": rt.running(sid)}
    except RuntimeError_ as err:
        raise HTTPException(409, str(err)) from err


@router.delete("/sessions/{sid}")
async def delete_session(sid: str, rt: Runtime = Depends(get_runtime)) -> dict:
    if rt.store.get(sid) is None:
        raise HTTPException(404, "没有这个会话。")
    try:
        await rt.delete(sid)
    except RuntimeError_ as err:
        raise HTTPException(409, str(err)) from err
    return {"ok": True}


class UndoTurn(BaseModel):
    turn_seq: int | None = Field(default=None, ge=0, strict=True)


@router.post("/sessions/{sid}/undo")
async def undo_turn(sid: str, req: UndoTurn | None = None, rt: Runtime = Depends(get_runtime)) -> Event:
    if rt.store.get(sid) is None:
        raise HTTPException(404, "没有这个会话。")
    try:
        return rt.undo(sid, req.turn_seq if req else None)
    except RuntimeError_ as err:
        raise HTTPException(409, str(err)) from err


class SetBudget(BaseModel):
    amount: float | None = Field(ge=0, allow_inf_nan=False, strict=True)
    _valid_amount = field_validator("amount", mode="before")(_budget_input)


@router.put("/sessions/{sid}/budget")
async def set_budget(sid: str, req: SetBudget, rt: Runtime = Depends(get_runtime)) -> Event:
    if rt.store.get(sid) is None:
        raise HTTPException(404, "没有这个会话。")
    try:
        return rt.set_budget(sid, req.amount)
    except RuntimeError_ as err:
        raise HTTPException(409, str(err)) from err


class SetOption(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=200)


@router.get("/sessions/{sid}/options")
async def engine_options(sid: str, rt: Runtime = Depends(get_runtime)) -> list[dict]:
    """本机 Codex / Claude Code 的模型、思考深度、快速模式（还没连上时先连上问它，要几秒）"""
    if rt.store.get(sid) is None:
        raise HTTPException(404, "没有这个会话。")
    try:
        return await rt.engine_options(sid)
    except RuntimeError_ as err:
        raise HTTPException(409, str(err)) from err


@router.put("/sessions/{sid}/options")
async def set_engine_option(sid: str, req: SetOption, rt: Runtime = Depends(get_runtime)) -> list[dict]:
    if rt.store.get(sid) is None:
        raise HTTPException(404, "没有这个会话。")
    try:
        return await rt.set_engine_option(sid, req.id, req.value)
    except RuntimeError_ as err:
        raise HTTPException(409, str(err)) from err


# ---------- 项目记忆 ----------


class MemoryNote(BaseModel):
    cwd: str = Field(min_length=1, max_length=1000)
    text: str = Field(min_length=1, max_length=2000)


class ForgetNote(BaseModel):
    cwd: str = Field(min_length=1, max_length=1000)
    id: str = Field(min_length=1, max_length=64)


@router.get("/commands")
async def list_commands(cwd: str | None = None, kind: Literal["project", "computer"] = "project",
                        rt: Runtime = Depends(get_runtime)) -> dict:
    """这个项目能用的自定义命令（输入「/」时列出来），以及命令文件放在哪"""
    found = rt.commands(cwd, kind)
    return {
        "commands": sorted((c.summary() for c in found.values()), key=lambda c: c["name"]),
        "project_dir": str(Path(cwd) / ".aila" / "commands") if cwd and kind != "computer" else None,
        "user_dir": str(rt.data_dir / "commands"),
    }


@router.get("/memory")
async def list_memory(cwd: str, rt: Runtime = Depends(get_runtime)) -> list[dict]:
    """这个项目记下的话，早的在前"""
    return [asdict(n) for n in rt.memory.notes(cwd)]


@router.post("/memory", status_code=201)
async def add_memory(req: MemoryNote, rt: Runtime = Depends(get_runtime)) -> dict:
    try:
        note, added = rt.memory.add(req.cwd, req.text, "user")
    except MemoryError_ as err:
        raise HTTPException(422, str(err)) from err
    return {**asdict(note), "added": added}


@router.delete("/memory")
async def forget_memory(req: ForgetNote, rt: Runtime = Depends(get_runtime)) -> dict:
    if not rt.memory.remove(req.cwd, req.id):
        raise HTTPException(404, "没有这条记忆，可能已经删掉了。")
    return {"ok": True}


# ---------- MCP 外部工具服务 ----------


class McpImport(BaseModel):
    json_text: str = Field(alias="json", min_length=2, max_length=50000)


class McpDisabled(BaseModel):
    disabled: bool


class McpForget(BaseModel):
    tool: str = Field(min_length=1, max_length=200)


def _mcp_name(name: str) -> str:
    if not name or len(name) > 32 or not all(c.isalnum() or c in "_-" for c in name):
        raise HTTPException(404, "没有这个服务。")
    return name


@router.get("/local")
async def local_agents(rt: Runtime = Depends(get_runtime)) -> list[dict]:
    """本机的 Codex、Claude Code：找到没有、登录没有、适配器装了没有（新任务里选谁做、设置页用）"""
    return [a.to_dict() for a in await asyncio.to_thread(detect, rt.data_dir)]


@router.post("/local/install")
async def local_install(rt: Runtime = Depends(get_runtime)) -> dict:
    """装 ACP 适配器（你在设置页点了才装；不带它们自带的 Codex / Claude，用你装好的）"""
    try:
        message = await install_adapters(rt.data_dir)
    except LocalAgentError as err:
        raise HTTPException(400, str(err)) from err
    return {"message": message, "agents": [a.to_dict() for a in await asyncio.to_thread(detect, rt.data_dir)]}


@router.get("/mcp")
async def mcp_status(rt: Runtime = Depends(get_runtime)) -> list[dict]:
    await rt.mcp.ensure_started()
    return rt.mcp.status()


@router.post("/mcp/preview")
async def mcp_preview(req: McpImport, rt: Runtime = Depends(get_runtime)) -> list[dict]:
    """保存之前给你看：会用什么命令启动哪些服务（由代码从配置拼出来）"""
    try:
        parsed = parse_import(req.json_text)
    except McpError as err:
        raise HTTPException(422, str(err)) from err
    existing = rt.mcp.configs()
    return [{"name": c.name, "command": c.command_line(), "envKeys": sorted(c.env), "exists": c.name in existing,
             "supported": bool(c.command)} for c in parsed.values()]


@router.post("/mcp/import")
async def mcp_import(req: McpImport, rt: Runtime = Depends(get_runtime)) -> list[dict]:
    try:
        parsed = parse_import(req.json_text)
    except McpError as err:
        raise HTTPException(422, str(err)) from err
    await rt.mcp.ensure_started()
    for config in parsed.values():
        await rt.mcp.save(config, keep_env=True)
    return rt.mcp.status()


@router.post("/mcp/{name}/restart")
async def mcp_restart(name: str, rt: Runtime = Depends(get_runtime)) -> list[dict]:
    try:
        await rt.mcp.restart(_mcp_name(name))
    except McpError as err:
        raise HTTPException(404, str(err)) from err
    return rt.mcp.status()


@router.put("/mcp/{name}/disabled")
async def mcp_disable(name: str, req: McpDisabled, rt: Runtime = Depends(get_runtime)) -> list[dict]:
    try:
        await rt.mcp.set_disabled(_mcp_name(name), req.disabled)
    except McpError as err:
        raise HTTPException(404, str(err)) from err
    return rt.mcp.status()


@router.delete("/mcp/{name}")
async def mcp_remove(name: str, rt: Runtime = Depends(get_runtime)) -> list[dict]:
    try:
        await rt.mcp.remove(_mcp_name(name))
    except McpError as err:
        raise HTTPException(404, str(err)) from err
    return rt.mcp.status()


@router.delete("/mcp/{name}/allow")
async def mcp_forget(name: str, req: McpForget, rt: Runtime = Depends(get_runtime)) -> list[dict]:
    if not rt.mcp.forget(_mcp_name(name), req.tool):
        raise HTTPException(404, "这个工具没有设成「以后都不用问」。")
    return rt.mcp.status()


# ---------- 长任务：按计划做 ----------


@router.post("/sessions/{sid}/plan/approve")
async def plan_approve(sid: str, req: PlanApprove, rt: Runtime = Depends(get_runtime)) -> StreamingResponse:
    _session_or_404(rt, sid)
    try:
        decision = rt.decide_plan(sid, True, req.pause_each)
        stream = rt.drive_plan(sid, after=decision["seq"] - 1)
    except RuntimeError_ as err:
        raise HTTPException(409, str(err)) from err
    return _sse(stream)


@router.post("/sessions/{sid}/plan/continue")
async def plan_continue(sid: str, rt: Runtime = Depends(get_runtime)) -> StreamingResponse:
    _session_or_404(rt, sid)
    try:
        stream = rt.drive_plan(sid)
    except RuntimeError_ as err:
        raise HTTPException(409, str(err)) from err
    return _sse(stream)


@router.post("/sessions/{sid}/plan/reject")
async def plan_reject(sid: str, rt: Runtime = Depends(get_runtime)) -> Event:
    _session_or_404(rt, sid)
    try:
        return rt.decide_plan(sid, False)
    except RuntimeError_ as err:
        raise HTTPException(409, str(err)) from err

