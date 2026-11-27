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


class IgnoreUpdate(BaseModel):
    patterns: list[str] = Field(default_factory=list, max_length=100)


@router.put("/code/projects/{project_id}/ignore")
async def set_ignore(project_id: str, req: IgnoreUpdate, projects: CodeProjects = Depends(get_code_projects)) -> dict[str, Any]:
    """改这个项目要忽略的文件夹（和项目里的 .ailaignore 合起来用），马上在后台重新扫：忽略掉的从索引里清出去"""
    try:
        project = projects.set_ignore(project_id, req.patterns)
    except KeyError as err:
        raise HTTPException(status_code=404, detail="没有这个项目") from err
    _start_refresh(projects, project_id)
    return asdict(project)


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
    churn = await asyncio.to_thread(projects.churn, project_id)
    return await asyncio.to_thread(index.graph, modified, churn)


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


# ---------- 看懂项目：整体架构 → 模块 → 文件接口链路 → 函数，每一层能让 AI 讲；出了问题顺着链路讲为什么 ----------
# 图和事实是代码算的（code/understand.py），讲解是 AI 说的（code/explain.py），界面上分开显示

_explain_cache: Any = None


def _cache() -> Any:
    global _explain_cache
    if _explain_cache is None:
        from ..code.explain import ExplainCache
        from ..deps import settings

        _explain_cache = ExplainCache(settings.data_dir / "understand-cache.json")
    return _explain_cache


def _model() -> Any:
    """讲解用的模型：设置里填的大模型；没填密钥是 None（只给事实）"""
    from ..deps import get_model, settings

    return None if settings.mock else get_model()


async def _project_graph(projects: CodeProjects, project_id: str) -> tuple[Any, dict[str, Any]]:
    from ..code.understand import clean

    if projects.get(project_id) is None:
        raise HTTPException(status_code=404, detail="没有这个项目")
    index = await asyncio.to_thread(projects.fresh, project_id)
    return index, clean(await asyncio.to_thread(index.graph))


def _known_file(graph: dict[str, Any], file: str) -> str:
    """只认索引里有的文件（不让请求读项目外面的东西）"""
    rel = file.strip().replace("\\", "/").strip("/")
    if rel not in {str(n["id"]) for n in graph.get("nodes", [])}:
        raise HTTPException(status_code=404, detail="项目里没有这个文件（或者还没建进索引）")
    return rel


def _readme(projects: CodeProjects, project_id: str, path: str) -> str:
    """这个目录下的 README 开头（讲整体时给 AI 参考）；只读项目里面的"""
    from pathlib import Path

    project = projects.get(project_id)
    if project is None:
        return ""
    root = Path(project.root).resolve()
    for name in ("README.md", "readme.md", "README.zh.md", "README"):
        target = (root / path / name).resolve()
        if target.is_relative_to(root) and target.is_file():
            try:
                return target.read_text(encoding="utf-8", errors="replace")[:2000]
            except OSError:
                return ""
    return ""


@router.get("/code/projects/{project_id}/area")
async def understand_area(project_id: str, path: str = "", tests: bool = False,
                          projects: CodeProjects = Depends(get_code_projects)) -> dict[str, Any]:
    """一层：path 为空是整体架构；点开一块就是它下面的子目录和文件，连线按种类汇总"""
    from ..code.understand import area

    _index, graph = await _project_graph(projects, project_id)
    return area(graph, path, tests)


@router.get("/code/projects/{project_id}/chain")
async def understand_chain(project_id: str, file: str = "", endpoint: str = "", down: int = 4, up: int = 2,
                           projects: CodeProjects = Depends(get_code_projects)) -> dict[str, Any]:
    """文件接口链路：从一个文件或者一个接口（HTTP 路径、IPC 频道）出发，往下连到最终执行的代码，往上找用到它的"""
    from ..code.understand import chain

    _index, graph = await _project_graph(projects, project_id)
    if file:
        file = _known_file(graph, file)
    return chain(graph, start=file, endpoint=endpoint[:300], down=max(1, min(down, 6)), up=max(0, min(up, 4)))


@router.get("/code/projects/{project_id}/endpoints")
async def understand_endpoints(project_id: str, projects: CodeProjects = Depends(get_code_projects)) -> list[dict[str, Any]]:
    """项目里认出来的 HTTP 接口和 IPC 频道（选「从接口看链路」用）"""
    from ..code.understand import endpoints

    _index, graph = await _project_graph(projects, project_id)
    return endpoints(graph)


def _file_detail(index: Any, graph: dict[str, Any], file: str) -> dict[str, Any]:
    uses = sorted({str(link["target"]) for link in graph["links"] if link["source"] == file and link["target"] != file})
    used_by = sorted({str(link["source"]) for link in graph["links"] if link["target"] == file and link["source"] != file})
    return {"file": file, "uses": uses, "usedBy": used_by, "outline": index.outline(file)}


def _brief(c: Any) -> dict[str, Any]:
    return {"kind": c.kind, "qname": c.qname, "file": c.file, "start": c.start, "end": c.end, "signature": c.signature}


@router.get("/code/projects/{project_id}/file")
async def understand_file(project_id: str, file: str, projects: CodeProjects = Depends(get_code_projects)) -> dict[str, Any]:
    """一个文件：里面的函数和类、它用到谁、谁用到它"""
    index, graph = await _project_graph(projects, project_id)
    detail = await asyncio.to_thread(_file_detail, index, graph, _known_file(graph, file))
    detail["outline"] = [{**_brief(c), "doc": c.doc} for c in detail["outline"]]
    return detail


def _function_detail(index: Any, file: str, line: int) -> dict[str, Any] | None:
    chunk = index.chunk_at(file, line)
    if chunk is None:
        return None
    end = min(chunk.end, chunk.start + 149)
    return {"chunk": chunk, "callers": index.callers(chunk), "callees": index.callees(chunk),
            "lines": index.source(chunk.file, chunk.start, end), "truncated": end < chunk.end}


@router.get("/code/projects/{project_id}/function")
async def understand_function(project_id: str, file: str, line: int,
                              projects: CodeProjects = Depends(get_code_projects)) -> dict[str, Any]:
    """一个函数：源码（最多 150 行）、谁调用它、它调用谁"""
    index, graph = await _project_graph(projects, project_id)
    detail = await asyncio.to_thread(_function_detail, index, _known_file(graph, file), line)
    if detail is None:
        raise HTTPException(status_code=404, detail="这一行不在任何函数里")
    return {**_brief(detail["chunk"]), "doc": detail["chunk"].doc, "lines": detail["lines"], "truncated": detail["truncated"],
            "callers": [_brief(c) for c in detail["callers"]], "callees": [_brief(c) for c in detail["callees"]]}


class ExplainRequest(BaseModel):
    level: str = Field(pattern=r"^(area|chain|file|function)$")
    path: str = Field(default="", max_length=1000)
    file: str = Field(default="", max_length=1000)
    line: int = Field(default=0, ge=0)
    endpoint: str = Field(default="", max_length=300)
    depth: str = Field(default="beginner", pattern=r"^(beginner|dev)$")


@router.post("/code/projects/{project_id}/explain")
async def understand_explain(project_id: str, req: ExplainRequest,
                             projects: CodeProjects = Depends(get_code_projects)) -> dict[str, Any]:
    """让 AI 讲一层。事实由这边按 level 和位置重新算（不收页面传来的事实），讲解按内容缓存"""
    from ..code import explain as ex
    from ..code.understand import area, chain, link_text

    index, graph = await _project_graph(projects, project_id)
    code = ""
    if req.level == "area":
        view = area(graph, req.path)
        facts = ex.area_facts(view, link_text, await asyncio.to_thread(_readme, projects, project_id, view["path"]))
    elif req.level == "chain":
        file = _known_file(graph, req.file) if req.file else ""
        view = chain(graph, start=file, endpoint=req.endpoint)
        if not view["nodes"]:
            raise HTTPException(status_code=404, detail="从这里连不出链路")
        facts = ex.chain_facts(view)
    elif req.level == "file":
        file = _known_file(graph, req.file)
        detail = await asyncio.to_thread(_file_detail, index, graph, file)
        facts = ex.file_facts(file, detail["outline"], detail["uses"], detail["usedBy"])
        head = await asyncio.to_thread(index.source, file, 1, 80)
        code = f"{file} 开头：\n{ex.numbered(head, 1)}"
    else:
        file = _known_file(graph, req.file)
        detail = await asyncio.to_thread(_function_detail, index, file, req.line)
        if detail is None:
            raise HTTPException(status_code=404, detail="这一行不在任何函数里")
        facts = ex.function_facts(detail["chunk"], detail["callers"], detail["callees"])
        code = f"{file}：\n{ex.numbered(detail['lines'], detail['chunk'].start)}"
    return await ex.explain(_model(), _cache(), req.level, req.depth, facts, code)


class DiagnoseRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    depth: str = Field(default="beginner", pattern=r"^(beginner|dev)$")


@router.post("/code/projects/{project_id}/diagnose")
async def understand_diagnose(project_id: str, req: DiagnoseRequest,
                              projects: CodeProjects = Depends(get_code_projects)) -> dict[str, Any]:
    """出问题了：报错落在哪个函数、是谁一路调用过来的（图上标出这些文件），再让 AI 根据这些函数的原文讲为什么"""
    from ..code import explain as ex

    index, _graph = await _project_graph(projects, project_id)
    located = [loc for loc in await asyncio.to_thread(index.locate, req.text) if loc.chunk]
    if not located:
        return {"found": False, "frames": [], "chain": [], "files": [], "facts": "", "explanation": None, "cached": False,
                "note": "报错里没找到这个项目的文件和行号：贴完整的报错（带堆栈）再试"}
    first = located[0].chunk
    assert first is not None
    callers = await asyncio.to_thread(index.chain, first, 3, 2)
    frames = [{"file": loc.file, "line": loc.frame.line, "qname": loc.chunk.qname if loc.chunk else None} for loc in located]
    files: list[str] = []
    for f in [loc.file for loc in located] + [c.file for c in callers]:
        if f and f not in files:
            files.append(f)
    facts = "\n".join([
        f"报错原文（开头）：\n{req.text[:2500]}",
        "出错位置（离出错点最近的在前）：",
        *[f"- {f['file']} 第 {f['line']} 行，在 {f['qname']} 里" for f in frames[:8]],
        "调用链（谁调用了出错的函数）：" + ("、".join(f"{c.qname}（{c.file}）" for c in callers[:8]) or "没找到"),
    ])
    parts = []
    for chunk in [first, *callers[:2]]:
        end = min(chunk.end, chunk.start + (119 if chunk is first else 39))
        source = await asyncio.to_thread(index.source, chunk.file, chunk.start, end)
        parts.append(f"{chunk.file}（{chunk.qname}）：\n{ex.numbered(source, chunk.start)}")
    answer = await ex.explain(_model(), _cache(), "diagnose", req.depth, facts, "\n\n".join(parts))
    return {"found": True, "frames": frames, "chain": [_brief(c) for c in callers], "files": files, **answer}
