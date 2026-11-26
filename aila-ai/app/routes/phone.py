"""设置 → 手机 用的本机接口：开关局域网的门、拿配对码、看和删配过的手机（门本身见 app/phone）。

这些接口和别的本机接口一样只认本机（security.py）；手机进不来这里。
"""

import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import deps
from ..notify import check_push_url, send_push
from ..phone import PAIR_SECONDS, lan_addresses

router = APIRouter(prefix="/phone")


class PushSet(BaseModel):
    kind: str = Field(pattern="^(none|ntfy|bark|wecom|feishu)$")
    # 空着表示不改（地址里常带密钥，设置页不回显，只在换的时候填）
    url: str = Field(default="", max_length=2000)


class PhoneSet(BaseModel):
    enabled: bool | None = None
    # 桌面端的技能包开关（关掉的技能包手机上也用不了）
    skills: dict[str, bool] | None = Field(default=None, max_length=200)
    # 推到手机通知栏
    push: PushSet | None = None


def status() -> dict[str, Any]:
    store, server = deps.get_phone(), deps.get_phone_server()
    pairing = store.pairing if store.pairing and store.pairing.expires > time.time() else None
    return {
        "enabled": store.enabled,
        "running": server.running,
        "error": server.error,
        "port": server.port,
        "addresses": [f"http://{a}:{server.port}" for a in lan_addresses()] if server.running else [],
        "devices": [d.public() for d in store.devices],
        "pairing": {"code": pairing.code, "expires": pairing.expires} if pairing else None,
        "push": {"kind": store.push_kind, "set": bool(store.push_url)},
    }


@router.get("")
async def get_phone() -> dict[str, Any]:
    return status()


@router.put("")
async def set_phone(req: PhoneSet) -> dict[str, Any]:
    store = deps.get_phone()
    if req.skills is not None:
        store.remember_skills(req.skills)
    if req.push is not None:
        if req.push.kind == "none":
            store.push_kind, store.push_url = "none", ""
        else:
            url = req.push.url.strip() or (store.push_url if req.push.kind == store.push_kind else "")
            if not url:
                raise HTTPException(400, "填上推送地址。")
            if problem := check_push_url(url):
                raise HTTPException(400, problem)
            store.push_kind, store.push_url = req.push.kind, url
        store.save()
    if req.enabled is not None and req.enabled != store.enabled:
        store.enabled = req.enabled
        store.pairing = None
        store.save()
        await deps.apply_phone()
    return status()


@router.post("/push-test")
async def push_test() -> dict[str, bool]:
    """推一条试试，看手机上收不收得到"""
    store = deps.get_phone()
    if store.push_kind == "none" or not store.push_url:
        raise HTTPException(409, "还没设推送。")
    try:
        await send_push(deps.http_client, store.push_kind, store.push_url, "测试", "能收到这条，说明推送设好了。")
    except Exception as err:
        raise HTTPException(502, f"没推出去（{type(err).__name__}）：看看地址对不对。") from err
    return {"ok": True}


@router.post("/pair")
async def new_code() -> dict[str, Any]:
    store = deps.get_phone()
    if not store.enabled:
        raise HTTPException(409, "先打开「允许手机连接」。")
    p = store.new_pairing()
    return {"code": p.code, "expires": p.expires, "seconds": PAIR_SECONDS}


@router.delete("/devices/{device_id}")
async def forget(device_id: str) -> dict[str, Any]:
    if not deps.get_phone().forget(device_id):
        raise HTTPException(404, "没有这台手机。")
    return status()
