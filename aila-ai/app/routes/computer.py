"""电脑环境预览与新建任务使用同一段快照生成逻辑。只返回目录和程序概况。"""

import asyncio
from pathlib import Path

from fastapi import APIRouter

from .. import deps
from ..computer import build_computer_context, computer_shell

router = APIRouter(prefix="/agent/computer")


@router.get("/context")
async def context() -> dict:
    enabled = deps.settings.computer_inventory
    text = await asyncio.to_thread(build_computer_context, Path.home(), computer_shell().label, enabled)
    return {"context": text, "inventory": enabled}
