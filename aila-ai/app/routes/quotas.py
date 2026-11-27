"""桌面主窗口的用量面板。额度采集、记录和关联设置都走本机服务。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, StringConstraints, model_validator

from ..deps import get_runtime
from ..runtime.quotas import QuotaBoard, board
from ..runtime.runner import Runtime

router = APIRouter(prefix="/agent/quotas")

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
Key = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_-]{1,40}$")]


class Record(BaseModel):
    provider: Key | None = None
    name: ShortText | None = None
    window: Key | None = None
    label: ShortText
    remaining: float = Field(ge=0, le=1e12, strict=True)
    total: float | None = Field(default=None, gt=0, le=1e12, strict=True)
    unit: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=16)]
    reset_at: float | None = Field(default=None, gt=0, le=253402300799, strict=True)

    @model_validator(mode="after")
    def consistent(self):
        if self.provider is None and (self.name is None or self.window is not None):
            raise ValueError("添加平台需要填写名称。")
        if self.total is not None and self.remaining > self.total:
            raise ValueError("剩余量不能超过总量。")
        return self


class CursorLink(BaseModel):
    linked: bool = Field(strict=True)


def get_board(runtime: Runtime = Depends(get_runtime)) -> QuotaBoard:
    return board(runtime.data_dir)


@router.get("")
async def list_quotas(refresh: bool = False, quota: QuotaBoard = Depends(get_board)):
    return await quota.refresh() if refresh else quota.view()


@router.post("/record")
async def record_usage(body: Record, quota: QuotaBoard = Depends(get_board)):
    try:
        return await quota.record(body.provider, body.name or "", body.window, body.label, body.remaining,
                                  body.total, body.unit, body.reset_at)
    except ValueError as err:
        raise HTTPException(422, str(err)) from err


@router.post("/claude")
async def claude_statusline(quota: QuotaBoard = Depends(get_board)):
    return quota.claude_config()


@router.put("/cursor")
async def link_cursor(body: CursorLink, quota: QuotaBoard = Depends(get_board)):
    return await quota.link_cursor(body.linked)
