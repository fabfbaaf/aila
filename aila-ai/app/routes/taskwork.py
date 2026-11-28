"""任务管理页的本地接口。排队后仍按普通编程任务的权限和事件流运行。"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, StringConstraints
from typing_extensions import Annotated

from ..code.fix import FixError
from ..deps import get_runtime
from ..i18n import localize
from ..runtime.branch import BranchError
from ..runtime.localagents import AgentEngine, LocalAgentError
from ..runtime.runner import Runtime, RuntimeError_
from ..runtime.taskwork import ModelPick, TaskWorkError

router = APIRouter(prefix="/agent/taskwork", tags=["agent"])
PathText = Annotated[str, StringConstraints(min_length=1, max_length=1024)]
TitleText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
KeyText = Annotated[str, StringConstraints(pattern=r"^[\w-]{1,80}$")]
NativeEngine = Literal["codex", "codex-alt", "claude", "claude-alt"]


class Pick(BaseModel):
    engine: AgentEngine | None = None
    model: str = Field(default="", max_length=200)


class QueueRequest(BaseModel):
    cwd: PathText
    title: TitleText
    prompt: str = Field(min_length=1, max_length=20000)
    dependencies: list[KeyText] = Field(default_factory=list, max_length=30)
    pick: Pick = Field(default_factory=Pick)
    fallback: Pick | None = None
    isolate: bool = True


class LimitRequest(BaseModel):
    cwd: PathText
    limit: int = Field(ge=1, le=12)


class VerdictRequest(BaseModel):
    sid: KeyText
    accepted: bool


class ImportRequest(BaseModel):
    cwd: PathText
    engine: NativeEngine
    native_id: str = Field(min_length=1, max_length=200)
    title: str = Field(default="", max_length=100)


class GroupRequest(BaseModel):
    title: TitleText
    repos: list[PathText] = Field(min_length=2, max_length=8)
    contract: str = Field(min_length=1, max_length=6000)
    acceptance: str = Field(min_length=1, max_length=6000)
    pick: Pick = Field(default_factory=Pick)


class GroupSessionRequest(BaseModel):
    group: KeyText
    member: KeyText | None = None


errors = (TaskWorkError, RuntimeError_, LocalAgentError, FixError, BranchError, OSError)


@router.get("")
async def state(rt: Runtime = Depends(get_runtime)) -> dict:
    result = rt.taskwork.state()
    for item in result["queue"]:
        item["error"] = localize(item["error"])
    return result


@router.get("/models")
async def recommendation(cwd: PathText, rt: Runtime = Depends(get_runtime)) -> dict:
    try:
        return rt.taskwork.recommend(cwd)
    except errors as err:
        raise HTTPException(400, str(err)) from err


@router.put("/limit")
async def limit(req: LimitRequest, rt: Runtime = Depends(get_runtime)) -> dict:
    try:
        rt.taskwork.set_limit(req.cwd, req.limit)
    except errors as err:
        raise HTTPException(400, str(err)) from err
    return {"ok": True}


@router.post("/queue", status_code=201)
async def enqueue(req: QueueRequest, rt: Runtime = Depends(get_runtime)) -> dict:
    try:
        pick = ModelPick(**req.pick.model_dump())
        fallback = ModelPick(**req.fallback.model_dump()) if req.fallback else None
        item = rt.taskwork.enqueue(req.cwd, req.title, req.prompt, req.dependencies, pick, fallback, req.isolate)
    except errors as err:
        raise HTTPException(400, str(err)) from err
    return {"id": item.id}


@router.delete("/queue/{key}")
async def cancel(key: KeyText, rt: Runtime = Depends(get_runtime)) -> dict:
    try:
        rt.taskwork.cancel(key)
    except errors as err:
        raise HTTPException(409, str(err)) from err
    return {"ok": True}


@router.post("/verdict")
async def verdict(req: VerdictRequest, rt: Runtime = Depends(get_runtime)) -> dict:
    try:
        rt.taskwork.verdict(req.sid, req.accepted)
    except errors as err:
        raise HTTPException(409, str(err)) from err
    return {"ok": True}


@router.get("/native")
async def native_list(engine: NativeEngine, cwd: PathText, cursor: str | None = None,
                      rt: Runtime = Depends(get_runtime)) -> dict:
    if cursor and len(cursor) > 4096:
        raise HTTPException(400, "翻页信息太长了。")
    try:
        return await rt.taskwork.discover(engine, cwd, cursor)
    except errors as err:
        raise HTTPException(400, str(err) or "原生适配器暂时连不上。") from err


@router.post("/native", status_code=201)
async def adopt(req: ImportRequest, rt: Runtime = Depends(get_runtime)) -> dict:
    try:
        return await rt.taskwork.adopt(req.engine, req.cwd, req.native_id, req.title)
    except errors as err:
        raise HTTPException(400, str(err) or "没能加载原会话。") from err


@router.post("/groups", status_code=201)
async def group(req: GroupRequest, rt: Runtime = Depends(get_runtime)) -> dict:
    if not req.contract.strip() or not req.acceptance.strip():
        raise HTTPException(400, "填好共享接口和联调验收条件。")
    try:
        return rt.taskwork.create_group(req.title, req.repos, req.contract, req.acceptance,
                                        ModelPick(**req.pick.model_dump()))
    except errors as err:
        raise HTTPException(400, str(err)) from err


@router.post("/group-session")
async def group_session(req: GroupSessionRequest, rt: Runtime = Depends(get_runtime)) -> dict:
    try:
        return {"sid": rt.taskwork.group_session(req.group, req.member)}
    except errors as err:
        raise HTTPException(409, str(err)) from err
