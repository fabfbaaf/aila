"""设置 → 手机 用的本机接口：开关局域网的门、拿配对码、看和删配过的手机（门本身见 app/phone）。

这些接口和别的本机接口一样只认本机（security.py）；手机进不来这里。
"""

import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import deps
from ..phone import PAIR_SECONDS, lan_addresses

router = APIRouter(prefix="/phone")


class PhoneSet(BaseModel):
    enabled: bool | None = None
    # 桌面端的技能包开关（关掉的技能包手机上也用不了）
    skills: dict[str, bool] | None = Field(default=None, max_length=200)


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
    }


@router.get("")
async def get_phone() -> dict[str, Any]:
    return status()


@router.put("")
async def set_phone(req: PhoneSet) -> dict[str, Any]:
    store = deps.get_phone()
    if req.skills is not None:
        store.remember_skills(req.skills)
    if req.enabled is not None and req.enabled != store.enabled:
        store.enabled = req.enabled
        store.pairing = None
        store.save()
        await deps.apply_phone()
    return status()


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
