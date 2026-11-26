"""桌面管家读取和投递发现；偏好可以在对话或设置页修改。"""
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import deps
from ..discoveries import Discoveries
from ..memory.extract import SENSITIVE
from ..tools.reader import Page, fetch_page, make_resolver

router = APIRouter(prefix="/discoveries")
Source = Literal["gemini", "deepseek", "openai"]
Focus = Literal["all", "releases", "pricing", "web"]
Delivery = Literal["daily", "weekly", "history"]


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


class Rule(BaseModel):
    source: Source
    focuses: list[Focus] = Field(min_length=1, max_length=3)
    delivery: Delivery
    preference: str = Field(default="", max_length=180)


class Receipt(BaseModel):
    id: str = Field(pattern=r"^[0-9a-f]{64}$")


@router.get("")
def state(store: Discoveries = Depends(get_discoveries)) -> dict[str, Any]:
    return store.state()


@router.get("/history")
def history(source: Source | None = None, before: str | None = Query(default=None, pattern=r"^[0-9a-f]{64}$"),
            limit: int = Query(default=20, ge=1, le=50), store: Discoveries = Depends(get_discoveries)) -> dict[str, Any]:
    return store.history(source, before, limit)


@router.put("/topics")
def save_rule(req: Rule, store: Discoveries = Depends(get_discoveries)) -> dict[str, Any]:
    if SENSITIVE.search(req.preference):
        raise HTTPException(status_code=422, detail="关注条件里去掉密码或密钥再保存。")
    store.save_rule(req.source, req.focuses, req.delivery, req.preference)
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
