"""她的作品和日记：

  GET    /studio/works                    作品列表（最新的在前；会顺便按会话状态更新做没做完）
  POST   /studio/works                    让她做一件 {hints?: {memories, tasks, occasion}, idea?: {title, idea, kind, why}, budget?}
  GET    /studio/works/{id}               一件作品：说明（README）、文件列表、文件夹
  GET    /studio/works/{id}/raw/{path}    作品里的文件（网页类带最严的 CSP：沙盒、不能联网）
  POST   /studio/works/{id}/seen          你看过了（不再冒气泡）
  DELETE /studio/works/{id}               删掉（正在做的先打断）
  GET    /studio/diary                    她的日记（最新的在前）
  POST   /studio/diary                    写今天的日记 {day, facts}（桌面端晚上送来材料）
"""

import asyncio
import mimetypes
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..deps import get_diary, get_studio
from ..diary import Diary, DiaryError
from ..studio import DEFAULT_BUDGET, Hints, Studio, StudioError

router = APIRouter(prefix="/studio")

# 作品是她写的代码，只当作品看：沙盒（独立的源，碰不到本机服务的接口）、不能发请求、不能加载外面的东西
WORK_CSP = ("sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
            "img-src data: blob:; media-src data: blob:; font-src data:; connect-src 'none'; form-action 'none'")


class HintsIn(BaseModel):
    memories: list[str] = Field(default_factory=list, max_length=30)
    tasks: list[str] = Field(default_factory=list, max_length=20)
    occasion: str = Field(default="", max_length=60)


class IdeaIn(BaseModel):
    title: str = Field(min_length=1, max_length=40)
    idea: str = Field(min_length=1, max_length=300)
    kind: str = Field(default="web", pattern="^(web|script)$")
    why: str = Field(default="", max_length=200)


class StartWork(BaseModel):
    hints: HintsIn | None = None
    idea: IdeaIn | None = None
    budget: float = Field(default=DEFAULT_BUDGET, gt=0, le=10)


class DiaryIn(BaseModel):
    day: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    facts: list[str] = Field(default_factory=list, max_length=40)


@router.get("/works")
async def list_works(studio: Studio = Depends(get_studio)) -> list[dict[str, Any]]:
    return [asdict(w) for w in await asyncio.to_thread(studio.works)]


@router.post("/works", status_code=201)
async def start_work(req: StartWork, studio: Studio = Depends(get_studio)) -> dict[str, Any]:
    hints = Hints(**req.hints.model_dump()) if req.hints else None
    try:
        work = await studio.start(hints, req.idea.model_dump() if req.idea else None, req.budget)
    except StudioError as err:
        raise HTTPException(409, str(err)) from err
    return asdict(work)


@router.get("/works/{work_id}")
async def work_detail(work_id: str, studio: Studio = Depends(get_studio)) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(studio.detail, work_id)
    except StudioError as err:
        raise HTTPException(404, str(err)) from err


@router.get("/works/{work_id}/raw/{path:path}")
async def work_file(work_id: str, path: str, studio: Studio = Depends(get_studio)) -> Response:
    try:
        file = studio.file(work_id, path)
    except StudioError as err:
        raise HTTPException(404, str(err)) from err
    media = mimetypes.guess_type(file.name)[0] or "text/plain"
    if not media.startswith(("text/", "image/")) and media not in ("application/javascript", "application/json"):
        media = "text/plain"  # 别的类型一律当文本，不让浏览器去执行或下载
    headers = {"Content-Security-Policy": WORK_CSP, "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}
    charset = "; charset=utf-8" if media.startswith("text/") or media.endswith(("javascript", "json")) else ""
    return Response(file.read_bytes(), media_type=media + charset, headers=headers)


@router.post("/works/{work_id}/seen")
async def work_seen(work_id: str, studio: Studio = Depends(get_studio)) -> dict[str, bool]:
    try:
        studio.folder(work_id)
    except StudioError as err:
        raise HTTPException(404, str(err)) from err
    await asyncio.to_thread(studio.seen, work_id)
    return {"ok": True}


@router.delete("/works/{work_id}")
async def delete_work(work_id: str, studio: Studio = Depends(get_studio)) -> dict[str, bool]:
    try:
        await studio.delete(work_id)
    except StudioError as err:
        raise HTTPException(404, str(err)) from err
    return {"ok": True}


@router.get("/diary")
async def list_diary(diary: Diary = Depends(get_diary)) -> list[dict[str, Any]]:
    return await asyncio.to_thread(diary.entries)


@router.post("/diary", status_code=201)
async def write_diary(req: DiaryIn, diary: Diary = Depends(get_diary), studio: Studio = Depends(get_studio)) -> dict[str, Any]:
    works = [w for w in await asyncio.to_thread(studio.works) if w.status == "done"]
    try:
        return await diary.write(req.day, req.facts, works)
    except DiaryError as err:
        raise HTTPException(409, str(err)) from err
