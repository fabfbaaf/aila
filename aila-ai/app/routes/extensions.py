"""扩展包接口（格式和安全边界见 app/extensions.py）

  GET    /extensions                   装好的扩展：命令、本事、工具服务（含运行状态）、设置（密钥只说填没填）
  POST   /extensions/inspect           {path}：把 zip 或文件夹放进暂存区检查，返回 {token, plan}（装之前给你看的清单）
  POST   /extensions/install           {token}：看过清单、点了「装」才调；同 id 的旧版本先停掉服务再换
  POST   /extensions/discard           {token}：不装了，删掉暂存的
  PATCH  /extensions/{id}              {enabled?, settings?: {key: 值}, servers?: {服务名: 停用?}}
  DELETE /extensions/{id}              卸载：停掉它的服务，删掉文件夹

改完都会让编程那边按新配置补开、停掉工具服务，对话那边跟着重连（McpManager.reload → on_change）。
"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..deps import get_runtime
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
