"""扩展包接口（格式和安全边界见 app/extensions.py）

  GET    /extensions                   装好的扩展：命令、本事、工具服务（含运行状态）、设置（密钥只说填没填）、动作、台词、形象、页面
  GET    /extensions/companion         桌面伙伴用：启用的扩展带的动作（姿态段）、台词、能换的形象
  GET    /extensions/{id}/pages/{名字}  扩展自带的页面（沙盒 CSP，不能联网）
  GET    /extensions/catalog           目录里的扩展（目录地址在 PUT /extensions/catalogs 里设）
  PUT    /extensions/catalogs          {urls}：目录地址（https，或者本机的 catalog.json）
  POST   /extensions/catalog/inspect   {catalog, id}：从目录下载（校验 sha256）放进暂存区，返回 {token, plan}，后面和导入一样
  POST   /extensions/inspect           {path}：把 zip 或文件夹放进暂存区检查，返回 {token, plan}（装之前给你看的清单）
  POST   /extensions/install           {token}：看过清单、点了「装」才调；同 id 的旧版本先停掉服务再换
  POST   /extensions/discard           {token}：不装了，删掉暂存的
  PATCH  /extensions/{id}              {enabled?, settings?: {key: 值}, servers?: {服务名: 停用?}}
  DELETE /extensions/{id}              卸载：停掉它的服务，删掉文件夹

改完都会让编程那边按新配置补开、停掉工具服务，对话那边跟着重连（McpManager.reload → on_change）。
"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from .studio import WORK_CSP

import httpx

from .. import deps
from ..deps import get_runtime
from ..extension_catalog import browse, fetch_and_stage, load_sources, save_sources
from ..extensions import ExtensionError
from ..runtime.mcp import McpError
from ..runtime.runner import Runtime

router = APIRouter(prefix="/extensions")


class InspectRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1000)


class TokenRequest(BaseModel):
    token: str = Field(min_length=1, max_length=40)


class ExtensionUpdate(BaseModel):
    enabled: bool | None = None
    settings: dict[str, str] | None = None
    servers: dict[str, bool] | None = None  # 服务名 → 停用？


def _bad(err: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(err))


# 改完以后等刚开的工具服务起来再回话（最多这么久），界面上直接看到「运行中」或者出错原因
SETTLE_SECONDS = 30.0


async def _listing(rt: Runtime, settle: bool = False) -> dict:
    await rt.mcp.ensure_started()
    if settle:
        await rt.mcp.wait_ready(SETTLE_SECONDS)
    running = {s["name"]: s for s in rt.mcp.status()}
    out = []
    for ext in rt.extensions.installed():
        info = rt.extensions.describe(ext)
        for server in info["servers"]:
            state = running.get(server["serverName"], {})
            server["status"] = state.get("status", "off") if ext.enabled else "off"
            server["error"] = state.get("error", "")
            server["command"] = state.get("command", "")
            server["tools"] = state.get("tools", [])
            server["log"] = state.get("log", [])
        out.append(info)
    return {"extensions": out, "folder": str(rt.extensions.root)}


@router.get("")
async def list_extensions(rt: Runtime = Depends(get_runtime)) -> dict:
    return await _listing(rt)


@router.get("/companion")
async def companion(rt: Runtime = Depends(get_runtime)) -> dict:
    """桌面伙伴用：启用的扩展带的动作、台词、形象"""
    return rt.extensions.companion()


@router.get("/{ext_id}/pages/{name}")
async def page(ext_id: str, name: str, rt: Runtime = Depends(get_runtime)) -> Response:
    """扩展自带的页面：和她的作品一样当网页看，最严的 CSP（沙盒、不能联网、不加载外面的东西）"""
    try:
        path = rt.extensions.page(ext_id, name)
    except ExtensionError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    headers = {"Content-Security-Policy": WORK_CSP, "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}
    return Response(path.read_bytes(), media_type="text/html; charset=utf-8", headers=headers)


@router.post("/inspect")
async def inspect(req: InspectRequest, rt: Runtime = Depends(get_runtime)) -> dict:
    try:
        token, plan = rt.extensions.stage(Path(req.path.strip().strip('"')))
    except (ExtensionError, OSError, ValueError) as err:
        raise _bad(err) from err
    return {"token": token, "plan": plan}


@router.post("/install")
async def install(req: TokenRequest, rt: Runtime = Depends(get_runtime)) -> dict:
    staged = rt.extensions.root / ".staging" / req.token
    try:
        from ..extensions import read_manifest

        if staged.is_dir():
            await rt.mcp.stop_extension(read_manifest(staged).id)  # 升级：旧版的服务先停，文件才换得掉
        ext = rt.extensions.install(req.token)
    except (ExtensionError, OSError) as err:
        raise _bad(err) from err
    await rt.mcp.reload()
    return {"installed": ext.id, **await _listing(rt, settle=True)}


@router.post("/discard")
async def discard(req: TokenRequest, rt: Runtime = Depends(get_runtime)) -> dict:
    rt.extensions.discard(req.token)
    return {"ok": True}


@router.patch("/{ext_id}")
async def update(ext_id: str, req: ExtensionUpdate, rt: Runtime = Depends(get_runtime)) -> dict:
    try:
        if req.settings is not None:
            rt.extensions.set_settings(ext_id, req.settings)
        if req.enabled is not None:
            rt.extensions.set_enabled(ext_id, req.enabled)
        for server, disabled in (req.servers or {}).items():
            rt.extensions.set_server_disabled(ext_id, server, disabled)
    except ExtensionError as err:
        raise _bad(err) from err
    try:
        await rt.mcp.reload()
    except McpError as err:
        raise _bad(err) from err
    return await _listing(rt, settle=True)


@router.delete("/{ext_id}")
async def uninstall(ext_id: str, rt: Runtime = Depends(get_runtime)) -> dict:
    try:
        rt.extensions.get(ext_id)
        await rt.mcp.stop_extension(ext_id)
        rt.extensions.uninstall(ext_id)
    except (ExtensionError, OSError) as err:
        raise _bad(err) from err
    await rt.mcp.reload()
    return await _listing(rt)


# ---------- 目录（第三期） ----------


class CatalogSources(BaseModel):
    urls: list[str] = Field(default_factory=list, max_length=10)


class CatalogPick(BaseModel):
    catalog: str = Field(min_length=1, max_length=1000)
    id: str = Field(min_length=1, max_length=24)


@router.get("/catalog")
async def catalog(rt: Runtime = Depends(get_runtime)) -> dict:
    return {"sources": load_sources(rt.extensions), "catalogs": await browse(rt.extensions, deps.http_client)}


@router.put("/catalogs")
async def set_catalogs(req: CatalogSources, rt: Runtime = Depends(get_runtime)) -> dict:
    try:
        save_sources(rt.extensions, req.urls)
    except ExtensionError as err:
        raise _bad(err) from err
    return await catalog(rt)


@router.post("/catalog/inspect")
async def catalog_inspect(req: CatalogPick, rt: Runtime = Depends(get_runtime)) -> dict:
    try:
        token, plan = await fetch_and_stage(rt.extensions, req.catalog, req.id, deps.http_client)
    except (ExtensionError, OSError, httpx.HTTPError) as err:
        raise _bad(err) from err
    return {"token": token, "plan": plan}
