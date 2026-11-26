"""设置 → 手机 里「让她操作安卓手机」那一块用的本机接口：找没找到 ADB、连着哪些手机、开关、选手机、无线调试配对。

和别的本机接口一样只认本机（security.py）；手机那个门（app/phone）上没有这些。
"""

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import deps
from ..android import SERIAL, AdbError

router = APIRouter(prefix="/android")


class AndroidSet(BaseModel):
    enabled: bool | None = None
    serial: str | None = Field(default=None, max_length=64)


class PairIn(BaseModel):
    address: str = Field(min_length=3, max_length=270)
    code: str = Field(pattern=r"^\d{6}$")


class ConnectIn(BaseModel):
    address: str = Field(min_length=3, max_length=270)


async def status() -> dict[str, Any]:
    s = deps.get_android_settings()
    adb = deps.get_adb()
    devices: list[dict[str, str]] = []
    error = ""
    if adb is not None:
        try:
            devices = [d.public() for d in await adb.devices()]
        except AdbError as err:
            error = str(err)
    return {"enabled": s.enabled, "serial": s.serial, "adb": adb.path if adb else None, "devices": devices, "error": error}


@router.get("")
async def get_android() -> dict[str, Any]:
    return await status()


@router.put("")
async def set_android(req: AndroidSet) -> dict[str, Any]:
    s = deps.get_android_settings()
    if req.serial is not None:
        if req.serial and not SERIAL.match(req.serial):
            raise HTTPException(400, "手机的编号不对。")
        s.serial = req.serial
    if req.enabled is not None:
        s.enabled = req.enabled
    s.save()
    return await status()


async def _adb_call(fn: str, *args: str) -> dict[str, Any]:
    adb = deps.get_adb()
    if adb is None:
        raise HTTPException(409, "没找到 ADB：先装 Android SDK Platform-Tools。")
    try:
        out = await getattr(adb, fn)(*args)
    except AdbError as err:
        raise HTTPException(400, str(err)) from err
    return {"output": out, **(await status())}


@router.post("/pair")
async def pair(req: PairIn) -> dict[str, Any]:
    return await _adb_call("pair", req.address.strip(), req.code)


@router.post("/connect")
async def connect(req: ConnectIn) -> dict[str, Any]:
    return await _adb_call("connect", req.address.strip())
