"""手机进来的门：只放聊天、看任务、开任务、接着说、停下这几件事（为什么见 phone/__init__.py）。

- 除了手机页面本身和配对，每个请求都要带设备密钥（Authorization: Bearer），对不上一律 401
- 直接调本机服务里同样的函数（聊天、编程任务的 Runtime），不经过本机接口，所以不碰 LocalOnly
- 开任务只能在你登记过、做过任务的项目里开（手机不能随便指一个文件夹），权限固定「先问再改」；
  她要改文件、跑命令时照常在电脑上问你——手机上只显示「等你在电脑上同意」
- 聊天里她要确认的操作（比如在电脑的浏览器里打开网页）手机上能回答：那是你在手机上和她说的话
- 不加跨域头：只有这个门自己发的手机页面能用；页面带 CSP，只认自己的脚本
"""

import asyncio
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..runtime.runner import Runtime, RuntimeError_
from ..sse import with_heartbeat
from ..i18n import localize_event, localize_summary
from . import Device, PhoneStore

STATIC = Path(__file__).with_name("static")
SID = re.compile(r"^[\w-]{1,64}$")
HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                               "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


class PairIn(BaseModel):
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")
    name: str = Field(default="手机", max_length=40)


class Line(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=20000)


class ChatIn(BaseModel):
    messages: list[Line] = Field(min_length=1, max_length=200)


class ConfirmIn(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    approved: bool


class TaskIn(BaseModel):
    project: str = Field(min_length=1, max_length=1000)  # find_project 给的路径
    text: str = Field(min_length=1, max_length=8000)


class SayIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


Chat = Callable[[list[dict[str, str]]], Awaitable[StreamingResponse]]
AnswerChat = Callable[[str, bool], bool]


def _sse(events: AsyncIterator[dict[str, Any]]) -> StreamingResponse:
    import json

    async def body() -> AsyncIterator[str]:
        async for event in events:
            yield f"data: {json.dumps(localize_event(event), ensure_ascii=False)}\n\n"

    return StreamingResponse(with_heartbeat(body()), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


def create_gateway(store: PhoneStore, runtime: Callable[[], Runtime], chat: Chat, answer_chat: AnswerChat) -> FastAPI:
    app = FastAPI(title="艾拉 · 手机", docs_url=None, redoc_url=None, openapi_url=None)

    def device(authorization: str = Header(default="")) -> Device:
        token = authorization.removeprefix("Bearer ").strip()
        found = store.device(token) if token else None
        if found is None:
            raise HTTPException(401, "这台手机还没配对，或者已经在电脑上被删掉了。")
        return found

    def session_id(sid: str) -> str:
        if not SID.match(sid):
            raise HTTPException(404, "没有这个任务。")
        return sid

    # ---------- 页面和配对（不用密钥） ----------

    @app.get("/")
    async def page() -> FileResponse:
        return FileResponse(STATIC / "index.html", headers=HEADERS)

    @app.get("/app.js")
    async def script() -> FileResponse:
        return FileResponse(STATIC / "app.js", media_type="text/javascript", headers=HEADERS)

    @app.post("/pair")
    async def pair(req: PairIn) -> dict[str, str]:
        token = store.pair(req.code, req.name)
        if token is None:
            raise HTTPException(403, "配对码不对或者过期了。在电脑上 设置 → 手机 里拿一个新的。")
        return {"token": token}

    # ---------- 要密钥的 ----------

    @app.get("/api/me")
    async def me(d: Device = Depends(device)) -> dict[str, str]:
        return {"name": d.name}

    @app.post("/api/chat")
    async def phone_chat(req: ChatIn, _: Device = Depends(device)) -> StreamingResponse:
        if req.messages[-1].role != "user":
            raise HTTPException(422, "最后一条必须是你说的话。")
        return await chat([m.model_dump() for m in req.messages])

    @app.post("/api/chat/confirm")
    async def phone_confirm(req: ConfirmIn, _: Device = Depends(device)) -> dict[str, bool]:
        if not answer_chat(req.id, req.approved):
            raise HTTPException(404, "这个确认已经结束了。")
        return {"ok": True}

    @app.get("/api/projects")
    async def projects(_: Device = Depends(device)) -> list[dict[str, str]]:
        return [{"name": name, "path": path} for name, path in runtime().find_project("")]

    @app.get("/api/tasks")
    async def tasks(_: Device = Depends(device)) -> list[dict[str, Any]]:
        rt = runtime()
        return [{**localize_summary(s), "running": rt.running(s["id"])} for s in rt.store.list(limit=50)]

    @app.get("/api/tasks/{sid}")
    async def task(sid: str = Depends(session_id), _: Device = Depends(device)) -> dict[str, Any]:
        rt = runtime()
        try:
            session = rt.agent(sid).session
        except RuntimeError_ as err:
            raise HTTPException(404, str(err)) from err
        return {**localize_summary(session.summary()), "running": rt.running(sid),
                "events": [localize_event(e) for e in session.events]}

    @app.get("/api/tasks/{sid}/events")
    async def task_events(after: int = -1, sid: str = Depends(session_id), _: Device = Depends(device)) -> StreamingResponse:
        rt = runtime()
        try:
            rt.agent(sid)
        except RuntimeError_ as err:
            raise HTTPException(404, str(err)) from err
        return _sse(rt.subscribe(sid, after))

    @app.post("/api/tasks", status_code=201)
    async def new_task(req: TaskIn, _: Device = Depends(device)) -> dict[str, Any]:
        rt = runtime()
        # 只认登记过、做过任务的项目：手机不能指定任意文件夹
        known = {path for _, path in rt.find_project("")}
        if req.project not in known:
            raise HTTPException(400, "只能在登记过的项目里开任务。")
        try:
            session = rt.create(req.project, "ask")  # 手机上开的任务固定先问再改，同意在电脑上点
            rt.start(session.id, req.text)
        except RuntimeError_ as err:
            raise HTTPException(400, str(err)) from err
        return {**session.summary(), "running": True}

    @app.post("/api/tasks/{sid}/messages")
    async def say(req: SayIn, sid: str = Depends(session_id), _: Device = Depends(device)) -> dict[str, bool]:
        rt = runtime()
        try:
            rt.agent(sid)
            rt.start(sid, req.text)  # 在后台做，进度从 /events 看
        except RuntimeError_ as err:
            raise HTTPException(409, str(err)) from err
        return {"ok": True}

    @app.post("/api/tasks/{sid}/interrupt")
    async def interrupt(sid: str = Depends(session_id), _: Device = Depends(device)) -> dict[str, bool]:
        rt = runtime()
        try:
            rt.agent(sid)
        except RuntimeError_ as err:
            raise HTTPException(404, str(err)) from err
        return {"interrupted": rt.interrupt(sid)}

    return app


async def default_chat(messages: list[dict[str, str]]) -> StreamingResponse:
    """和电脑上一样的聊天（同一个模型、技能包开关、记忆库），只是不带桌面端那边的记忆列表"""
    from .. import deps
    from ..routes.chat import ChatRequest, chat

    embedder = await asyncio.to_thread(deps.get_embedder)
    # 技能包开关用桌面端的（关掉的手机上也用不了）
    req = ChatRequest(messages=messages, skills=deps.get_phone().skills)
    return await chat(req, model=deps.get_model(), registry=deps.get_registry(),
                      embedder=embedder, confirms=deps.get_confirmations(), store=deps.get_memory_store())


def default_answer(confirm_id: str, approved: bool) -> bool:
    from .. import deps

    return deps.get_confirmations().answer(confirm_id, approved)
