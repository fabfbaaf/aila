"""桌面管家读取和投递发现；偏好可以在对话或设置页修改。"""
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .. import deps
from ..discoveries import Discoveries
from ..tools.reader import Page, fetch_page, make_resolver

router = APIRouter(prefix="/discoveries")
Source = Literal["gemini", "deepseek", "openai"]


async def _read(url: str) -> Page:
    return await fetch_page(url, deps.http_client, make_resolver(deps.http_client), max_chars=32_000)


@lru_cache
def _store(data_dir: Path) -> Discoveries:
    return Discoveries(data_dir / "discoveries.sqlite3", _read, lambda: deps.get_model())


def get_discoveries() -> Discoveries:
    return _store(deps.settings.data_dir)


class Change(BaseModel):
    source: Source
    action: Literal["pause", "less"]


class Receipt(BaseModel):
    id: str = Field(pattern=r"^[0-9a-f]{64}$")


@router.get("")
def state(store: Discoveries = Depends(get_discoveries)) -> dict[str, Any]:
    return store.state()


@router.post("/poll")
async def poll(store: Discoveries = Depends(get_discoveries)) -> dict[str, Any] | None:
    if deps.settings.mock or not deps.settings.llm_api_key:
        return store.pending()
    return await store.poll()


@router.post("/topics")
def change(req: Change, store: Discoveries = Depends(get_discoveries)) -> dict[str, Any]:
    store.change(req.source, req.action)
    return store.state()


@router.post("/shown")
def shown(req: Receipt, store: Discoveries = Depends(get_discoveries)) -> dict[str, bool]:
    store.shown(req.id)
    return {"ok": True}
