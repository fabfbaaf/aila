"""编程 Agent 的接口（桌面工作窗口和终端都用这一套）。

  POST   /agent/sessions                   新建会话 {cwd, mode, isolate（在新分支上做）}
  POST   /agent/sessions/{id}/merge        在新分支上做时：跑测试、合并回你的代码 {run_tests}
  POST   /agent/sessions/{id}/discard      放弃分支（你的代码保持原样）
  POST   /agent/tasks                      新建会话并马上开始做 {text, cwd 或 project（代码库里的项目 id）, mode}
  GET    /agent/sessions                   会话列表（最近的在前）
  GET    /agent/sessions/{id}              会话的全部日志事件（界面据此重建）
  POST   /agent/sessions/{id}/messages     说一句话，开始一轮；返回这一轮的事件流（SSE）。连接断了她照样做完
  GET    /agent/sessions/{id}/events       订阅：补发 after 之后的事件，正在做的话接着推实时事件（SSE）
  POST   /agent/sessions/{id}/interrupt    打断
  PUT    /agent/sessions/{id}/mode         换权限模式 {mode}
  POST   /agent/sessions/{id}/compact      手动压缩上下文
  GET    /agent/sessions/{id}/context      上下文账本：上下文由哪几块组成、各占多少
  POST   /agent/confirm                    回答她的确认 {id, approved, remember}

和其他接口一样，只接受本机页面和本机程序的请求（见 security.py）。
"""

import json
from collections.abc import AsyncIterator
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..deps import get_runtime
from ..runtime.permissions import MODE_LABELS
from ..runtime.runner import Runtime, RuntimeError_
from ..runtime.session import Event

router = APIRouter(prefix="/agent")

ModeName = Literal["read_only", "ask", "auto"]


class NewSession(BaseModel):
    cwd: str = Field(min_length=1, max_length=1000)
    mode: ModeName = "ask"
    isolate: bool = False  # 在新分支上做


class Merge(BaseModel):
    run_tests: bool = True


class NewTask(BaseModel):
    """从别处交过来的任务（屏幕上的报错、星图、对话）：开一个会话，马上开始做"""

    text: str = Field(min_length=1, max_length=20000)
    cwd: str | None = Field(default=None, max_length=1000)
    project: str | None = Field(default=None, max_length=64)  # 代码库里登记的项目 id
    mode: ModeName = "ask"


class Say(BaseModel):
    text: str = Field(min_length=1, max_length=20000)


class SetMode(BaseModel):
    mode: ModeName


class Answer(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    approved: bool
    remember: bool = False


def _sse(events: AsyncIterator[Event]) -> StreamingResponse:
    async def body() -> AsyncIterator[str]:
        async for event in events:
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(body(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


def _session_or_404(rt: Runtime, sid: str):  # noqa: ANN202
    try:
        return rt.agent(sid).session
    except RuntimeError_ as err:
        raise HTTPException(404, str(err)) from err


@router.post("/sessions", status_code=201)
async def create_session(req: NewSession, rt: Runtime = Depends(get_runtime)) -> dict:
    try:
        session = rt.create(req.cwd, req.mode, req.isolate)
    except RuntimeError_ as err:
        raise HTTPException(400, str(err)) from err
    return {**session.summary(), "shell": rt.shell.label, "modes": MODE_LABELS}


@router.post("/tasks", status_code=201)
async def new_task(req: NewTask, rt: Runtime = Depends(get_runtime)) -> dict:
    try:
        cwd = rt.project_dir(req.project) if req.project else req.cwd
        if not cwd:
            raise RuntimeError_("要告诉我在哪个项目里做。")
        session = rt.create(cwd, req.mode)
        rt.start(session.id, req.text)
    except RuntimeError_ as err:
        raise HTTPException(400, str(err)) from err
    return {**session.summary(), "running": True}


@router.get("/sessions")
async def list_sessions(rt: Runtime = Depends(get_runtime), q: str | None = None, cwd: str | None = None) -> list[dict]:
    return [{**s, "running": rt.running(s["id"])} for s in rt.store.list(query=q, cwd=cwd)]


@router.get("/sessions/{sid}")
async def get_session(sid: str, rt: Runtime = Depends(get_runtime)) -> dict:
    session = _session_or_404(rt, sid)
    return {**session.summary(), "running": rt.running(sid), "shell": rt.shell.label, "events": session.events}


@router.post("/sessions/{sid}/messages")
async def say(sid: str, req: Say, rt: Runtime = Depends(get_runtime)) -> StreamingResponse:
    _session_or_404(rt, sid)
    try:
        stream = rt.run(sid, req.text)
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
    rt.set_mode(sid, req.mode)
    return {"mode": req.mode, "label": MODE_LABELS[req.mode]}


@router.get("/sessions/{sid}/context")
async def context(sid: str, rt: Runtime = Depends(get_runtime)) -> dict:
    """上下文账本：下一步发给模型的上下文由哪几块组成、各占多少、命中缓存多少"""
    _session_or_404(rt, sid)
    return rt.context(sid)


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
