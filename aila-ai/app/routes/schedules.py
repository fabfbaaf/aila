"""定时任务的本机接口（设置 → 编程 →「定时任务」用）：列出、加、改、删、现在跑一次。规则见 app/runtime/schedules.py。

和别的本机接口一样只认本机（security.py）；手机那个门上没有这些。
"""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import deps
from ..runtime.schedules import Schedule, next_run

router = APIRouter(prefix="/agent/schedules")


class ScheduleIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    project: str = Field(min_length=1, max_length=1000)
    prompt: str = Field(min_length=1, max_length=4000)
    kind: str = "daily"
    time: str = "09:00"
    days: list[int] = Field(default_factory=lambda: list(range(7)), max_length=7)
    hours: int = 24
    action: str = "run"
    mode: str = "read_only"
    enabled: bool = True


class ScheduleChange(BaseModel):
    name: str | None = Field(default=None, max_length=40)
    project: str | None = Field(default=None, max_length=1000)
    prompt: str | None = Field(default=None, max_length=4000)
    kind: str | None = None
    time: str | None = None
    days: list[int] | None = Field(default=None, max_length=7)
    hours: int | None = None
    action: str | None = None
    mode: str | None = None
    enabled: bool | None = None


def known_projects() -> set[str]:
    return {path for _, path in deps.get_runtime().find_project("")}


def view(s: Schedule) -> dict[str, Any]:
    now = datetime.now().astimezone()
    from dataclasses import asdict

    return {**asdict(s), "describe": s.describe(), "next": next_run(s, now).isoformat() if s.enabled else None}


@router.get("")
async def list_schedules() -> dict[str, Any]:
    projects = [{"name": name, "path": path} for name, path in deps.get_runtime().find_project("")]
    return {"items": [view(s) for s in deps.get_schedules().items], "projects": projects}


@router.post("", status_code=201)
async def add_schedule(req: ScheduleIn) -> dict[str, Any]:
    if req.project not in known_projects():
        raise HTTPException(400, "只能在登记过、做过任务的项目里设定时任务。")
    try:
        s = deps.get_schedules().add(Schedule(id="", **req.model_dump()), datetime.now().astimezone())  # type: ignore[arg-type]
    except ValueError as err:
        raise HTTPException(400, str(err)) from err
    return view(s)


@router.put("/{sid}")
async def change_schedule(sid: str, req: ScheduleChange) -> dict[str, Any]:
    changes = {k: v for k, v in req.model_dump().items() if v is not None}
    if "project" in changes and changes["project"] not in known_projects():
        raise HTTPException(400, "只能在登记过、做过任务的项目里设定时任务。")
    try:
        return view(deps.get_schedules().update(sid, changes))
    except KeyError as err:
        raise HTTPException(404, "没有这个定时任务。") from err
    except ValueError as err:
        raise HTTPException(400, str(err)) from err


@router.delete("/{sid}")
async def delete_schedule(sid: str) -> dict[str, bool]:
    if not deps.get_schedules().remove(sid):
        raise HTTPException(404, "没有这个定时任务。")
    return {"ok": True}


@router.post("/{sid}/run")
async def run_now(sid: str) -> dict[str, Any]:
    s = deps.get_schedules().get(sid)
    if s is None:
        raise HTTPException(404, "没有这个定时任务。")
    return view(await deps.get_scheduler().fire(s))
