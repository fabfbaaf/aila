"""资料库：上传、列表、删除、检索（检索主要由她在对话里通过 search_knowledge 工具调用，这里方便调试）"""

import asyncio
from dataclasses import asdict
from typing import Any
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..deps import get_knowledge
from ..knowledge import DocumentError, KnowledgeBase
from ..knowledge.parse import MAX_BYTES

router = APIRouter(prefix="/knowledge")


@router.get("")
def documents(kb: KnowledgeBase = Depends(get_knowledge)) -> list[dict[str, Any]]:
    return [asdict(d) for d in kb.documents()]


@router.post("")
async def upload(request: Request, name: str, kb: KnowledgeBase = Depends(get_knowledge)) -> dict[str, Any]:
    """请求体就是文件内容，文件名放在 ?name= 里（不用 multipart，少一个依赖）。解析和算向量可能要几秒到几十秒"""
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="文件是空的")
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="文件太大了（最多 20MB）")
    try:
        doc = await asyncio.to_thread(kb.add, unquote(name), data)
    except DocumentError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    return asdict(doc)


@router.delete("/{doc_id}", status_code=204)
def delete(doc_id: str, kb: KnowledgeBase = Depends(get_knowledge)) -> None:
    if not kb.delete(doc_id):
        raise HTTPException(status_code=404, detail="没有这份资料")


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    k: int = Field(default=5, ge=1, le=20)


@router.post("/search")
async def search(req: SearchRequest, kb: KnowledgeBase = Depends(get_knowledge)) -> list[dict[str, Any]]:
    hits = await asyncio.to_thread(kb.search, req.query, req.k)
    return [{**{k: v for k, v in asdict(h).items() if k != "sha256"}, "source": h.source} for h in hits]
