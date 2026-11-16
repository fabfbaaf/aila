"""代码库：登记项目、建索引（后台）、搜索、按报错定位。设置窗口和调试用；她自己用的是「代码库」技能包里的工具"""

import asyncio
import logging
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..code import CodeProjects
from ..code.fix import fix_stats
from ..deps import get_code_projects

log = logging.getLogger("aila")
router = APIRouter()
_tasks: set[asyncio.Task[Any]] = set()


class AddProject(BaseModel):
    path: str = Field(min_length=1, max_length=1000)


class CodeQuery(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=6, ge=1, le=20)


class LocateQuery(BaseModel):
    text: str = Field(min_length=1, max_length=20000)


def _start_refresh(projects: CodeProjects, project_id: str) -> None:
    async def run() -> None:
        try:
            stats = await asyncio.to_thread(projects.refresh, project_id)
            log.info("代码索引更新完：%s", stats)
        except Exception as err:
            log.warning("代码索引失败：%s", err)

    projects.status[project_id] = {"state": "indexing"}
    task = asyncio.create_task(run())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


@router.get("/code/projects")
def list_projects(projects: CodeProjects = Depends(get_code_projects)) -> list[dict[str, Any]]:
    return projects.describe()


@router.get("/code/fixes")
def fixes(projects: CodeProjects = Depends(get_code_projects)) -> dict[str, int]:
    """她修过几次代码、跑了测试的几次、测试通过几次、改进用户代码几次"""
    return fix_stats(projects.fix_log)


@router.post("/code/projects")
async def add_project(req: AddProject, projects: CodeProjects = Depends(get_code_projects)) -> dict[str, Any]:
    """登记一个项目文件夹，马上在后台建索引（第一次要算向量，几百个文件大约一分钟）"""
    try:
        project = projects.add(req.path)
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    _start_refresh(projects, project.id)
    return asdict(project)


@router.post("/code/projects/{project_id}/refresh")
async def refresh_project(project_id: str, projects: CodeProjects = Depends(get_code_projects)) -> dict[str, bool]:
    if projects.get(project_id) is None:
        raise HTTPException(status_code=404, detail="没有这个项目")
    _start_refresh(projects, project_id)
    return {"ok": True}


@router.delete("/code/projects/{project_id}")
def delete_project(project_id: str, projects: CodeProjects = Depends(get_code_projects)) -> dict[str, bool]:
    """不再登记这个项目，删掉它的索引（项目里的代码不动）"""
    if not projects.remove(project_id):
        raise HTTPException(status_code=404, detail="没有这个项目")
    return {"ok": True}


@router.post("/code/projects/{project_id}/search")
async def search(project_id: str, req: CodeQuery, projects: CodeProjects = Depends(get_code_projects)) -> list[dict[str, Any]]:
    if projects.get(project_id) is None:
        raise HTTPException(status_code=404, detail="没有这个项目")
    hits = await asyncio.to_thread(projects.index(project_id).search, req.query, req.k)
    return [{k: v for k, v in asdict(h).items() if k != "text"} for h in hits]


def _project_index(projects: CodeProjects, project_id: str) -> Any:
    if projects.get(project_id) is None:
        raise HTTPException(status_code=404, detail="没有这个项目")
    return projects.index(project_id)


@router.get("/code/projects/{project_id}/graph")
async def graph(project_id: str, projects: CodeProjects = Depends(get_code_projects)) -> dict[str, Any]:
    """星图：文件是星球（大小看行数、颜色看子项目、没提交的改动亮一点），文件之间的调用是光线"""
    _project_index(projects, project_id)
    index = await asyncio.to_thread(projects.fresh, project_id)  # 新加的、改过的文件先更新进去
    modified = await asyncio.to_thread(projects.modified_files, project_id)
    return await asyncio.to_thread(index.graph, modified)


@router.get("/code/projects/{project_id}/outline")
async def outline(project_id: str, file: str, projects: CodeProjects = Depends(get_code_projects)) -> list[dict[str, Any]]:
    """一个文件里的函数和类（全息面板列出来）"""
    index = _project_index(projects, project_id)
    chunks = await asyncio.to_thread(index.outline, file)
    return [{"kind": c.kind, "qname": c.qname, "start": c.start, "end": c.end, "signature": c.signature, "doc": c.doc}
            for c in chunks]


@router.get("/code/projects/{project_id}/chunk")
async def chunk(project_id: str, file: str, line: int, projects: CodeProjects = Depends(get_code_projects)) -> dict[str, Any]:
    """包含这一行的函数，带上现在磁盘上的代码（面板里预览）"""
    index = _project_index(projects, project_id)
    c = await asyncio.to_thread(index.chunk_at, file, line)
    if c is None:
        raise HTTPException(status_code=404, detail="这一行不在任何函数里")
    end = min(c.end, c.start + 199)
    return {"kind": c.kind, "qname": c.qname, "file": c.file, "start": c.start, "end": c.end,
            "lines": await asyncio.to_thread(index.source, c.file, c.start, end), "truncated": end < c.end}


class LocateAll(BaseModel):
    text: str = Field(min_length=1, max_length=20000)


@router.post("/code/locate")
async def locate_all(req: LocateAll, projects: CodeProjects = Depends(get_code_projects)) -> dict[str, Any] | None:
    """报错落在哪个登记的项目里、哪个函数、谁调用了它（星图把这颗星球标红、沿调用链高亮）。不在任何项目里时返回 null"""
    best = None
    for project in projects.all():
        index = await asyncio.to_thread(projects.fresh, project.id)
        located = [loc for loc in await asyncio.to_thread(index.locate, req.text) if loc.chunk]
        if located and (best is None or len(located) > len(best[2])):
            best = (project, index, located)
    if best is None:
        return None
    project, index, located = best
    first = located[0]
    assert first.chunk is not None
    chain = await asyncio.to_thread(index.chain, first.chunk)
    return {
        "project": project.id, "name": project.name,
        "frames": [{"file": loc.file, "line": loc.frame.line, "qname": loc.chunk.qname if loc.chunk else None} for loc in located],
        "chain": [{"file": c.file, "qname": c.qname, "line": c.start} for c in chain],
    }


@router.post("/code/projects/{project_id}/locate")
async def locate(project_id: str, req: LocateQuery, projects: CodeProjects = Depends(get_code_projects)) -> list[dict[str, Any]]:
    if projects.get(project_id) is None:
        raise HTTPException(status_code=404, detail="没有这个项目")
    located = await asyncio.to_thread(projects.index(project_id).locate, req.text)
    return [
        {"file": loc.frame.file, "line": loc.frame.line, "func": loc.frame.func, "project_file": loc.file,
         "chunk": loc.chunk.qname if loc.chunk else None}
        for loc in located
    ]
