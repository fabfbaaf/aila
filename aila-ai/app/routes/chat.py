"""对话：/chat（流式回复）、/chat/confirm（回答工具确认）、/memory/extract（整理记忆）、/skills（技能包）、/health"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import deps
from ..agent import run_agent
from ..confirm import Confirmations
from ..extension_extras import motions_prompt
from ..deps import get_confirmations, get_embedder, get_memory_store, get_model, get_registry, status
from ..llm import ChatModel, Message, classify_error
from ..memory import Memory, Turn, extract
from ..memory.episodes import Line, chat_context
from ..memory.store import EPISODE, MemoryStore
from ..persona import system_prompt
from ..i18n import localize_event
from ..sse import with_heartbeat
from ..tags import Event
from ..tools import SkillRegistry

log = logging.getLogger("aila")
router = APIRouter()


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class MemoryIn(BaseModel):
    """桌面端保存的一条记忆（云端同步过来的）"""

    id: str = Field(min_length=1, max_length=64)
    content: str = Field(min_length=1, max_length=500)
    kind: str = "other"
    expiresAt: datetime | None = None
    updatedAt: datetime | None = None

    def to_memory(self) -> Memory:
        return Memory(self.id, self.content, self.kind, _aware(self.expiresAt), _aware(self.updatedAt))


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=200)
    # 她记得的所有事：这里挑出和这次话题相关的几条放进提示词
    memories: list[MemoryIn] = Field(default_factory=list, max_length=2000)
    # 用户对技能包的开关（覆盖默认值），比如 {"web": false, "mcp:time": true}
    skills: dict[str, bool] = Field(default_factory=dict)


def _aware(t: datetime | None) -> datetime | None:
    return t.astimezone() if t is not None else None


def window_start(n: int, max_history: int) -> int:
    """对话窗口从第几句开始：太长时一次丢一批（max_history 的四分之一），前面的滑出窗口"""
    if n <= max_history:
        return 0
    chunk = max(2, max_history // 4)
    return -(-(n - max_history) // chunk) * chunk


def build_messages(history: list[ChatMessage], max_history: int, context: str = "", system: str = "") -> list[Message]:
    """固定的系统提示词 + 最近的对话；日期和记忆放在最后一句话前面的 <背景> 里。

    按 DeepSeek 的前缀缓存来排（和编程 Agent 一样）：
    - 系统提示词一个字不变（以前日期和记忆拼在里面，每次都变，后面的历史全部不命中）
    - 会变的背景只跟着最新一句：之前的对话原样不动，前缀能命中到上一句为止
    - 历史太长时一次丢一批（不是每轮丢最早的一条）：开头隔好几轮才变一次
    连续同一角色的消息合并，有些模型不接受连续两条用户消息。
    """
    recent = history[window_start(len(history), max_history):]
    while recent and recent[0].role != "user":
        recent = recent[1:]  # 截断后要从用户消息开始

    merged: list[Message] = []
    for m in recent:
        if merged and merged[-1]["role"] == m.role:
            merged[-1]["content"] += "\n" + m.content
        else:
            merged.append({"role": m.role, "content": m.content})
    if context and merged and merged[-1]["role"] == "user":
        merged[-1] = {"role": "user", "content": f"<背景>\n{context}\n</背景>\n\n{merged[-1]['content']}"}
    return [{"role": "system", "content": system or system_prompt()}, *merged]


async def memory_context(req: ChatRequest, embedder: Any, store: MemoryStore | None = None) -> str:
    """今天的日期 + 和最近几句话相关的记忆 + 更早对话的摘要和相关的经历（记忆库里的）"""
    now = datetime.now().astimezone()
    query = "\n".join(m.content for m in req.messages if m.role == "user")[-300:]
    memories = [m.to_memory() for m in req.memories]
    try:
        return await asyncio.to_thread(chat_context, store, memories, query, embedder, now)
    except Exception as err:  # 记忆库坏了也照常聊天：退回到不查库的挑法
        log.warning("记忆库读不了：%s", err)
        return await asyncio.to_thread(chat_context, None, memories, query, embedder, now)


def sse(event: Event) -> str:
    return f"data: {json.dumps(localize_event(event), ensure_ascii=False)}\n\n"  # 英文界面时给人看的字段换成英文


@router.get("/health")
def health(model: ChatModel = Depends(get_model), registry: SkillRegistry = Depends(get_registry)) -> dict[str, object]:
    return {
        "ok": True,
        "model": model.name,
        "tools": registry.toolbox().names,
        "voice": {"ears": status["ears"], "tts": status["tts"]},
        "embed": status["embed"],
    }


@router.get("/skills")
def skills(registry: SkillRegistry = Depends(get_registry)) -> list[dict[str, Any]]:
    """所有技能包和默认开关（桌面端右键菜单里显示）"""
    return registry.describe()


@router.post("/chat")
async def chat(
    req: ChatRequest,
    model: ChatModel = Depends(get_model),
    registry: SkillRegistry = Depends(get_registry),
    embedder: Any = Depends(get_embedder),
    confirms: Confirmations = Depends(get_confirmations),
    store: MemoryStore = Depends(get_memory_store),
) -> StreamingResponse:
    if req.messages[-1].role != "user":
        raise HTTPException(status_code=422, detail="最后一条必须是用户消息")
    s = deps.settings
    # 扩展包带的动作：系统提示词里列出来（装卸扩展时才变，平时前缀不变），标签解析也认
    motions = deps.get_extensions().motions()
    messages = build_messages(req.messages, s.max_history, await memory_context(req, embedder, store),
                              system_prompt(s.persona_style, s.persona_call, s.persona_catchphrase, s.language) + motions_prompt(motions))
    # 滑出对话窗口的那段：后台写成摘要存进记忆库（以后每次带上最新的一份）
    start = window_start(len(req.messages), s.max_history)
    if start:
        try:
            deps.digester.schedule(model, store, [Line(m.role, m.content) for m in req.messages[:start]])
        except Exception as err:
            log.warning("安排对话摘要失败：%s", err)
    # 这次能用哪些工具：用户开着的技能包里的（资料库空着时不给）
    toolbox = await asyncio.to_thread(registry.toolbox, req.skills)

    async def events() -> AsyncIterator[str]:
        try:
            async for event in run_agent(model, messages, toolbox, confirms, frozenset(m.name for m in motions)):
                yield sse(event)
            yield sse({"type": "done"})
        except Exception as err:  # 出错也要告诉桌面端，让艾拉有反应，而不是一直等
            code, message = classify_error(err)
            log.warning("大模型调用失败（%s）：%s", code, err)
            yield sse({"type": "error", "code": code, "message": message})

    # 客户端断开时 Starlette 会取消这个生成器，大模型请求和工具调用也随之停止，等着的确认也作废
    # 等你确认某个操作时可能好一会儿没事件：心跳让连接不被当成断了
    return StreamingResponse(with_heartbeat(events()), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


class ConfirmAnswer(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    approved: bool


@router.post("/chat/confirm")
async def chat_confirm(ans: ConfirmAnswer, confirms: Confirmations = Depends(get_confirmations)) -> dict[str, bool]:
    """用户对工具确认的回答（桌面端的「好 / 不要」）。要在事件循环里执行，所以是 async"""
    if not confirms.answer(ans.id, ans.approved):
        raise HTTPException(status_code=404, detail="这个确认已经结束了")
    return {"ok": True}


class ExtractRequest(BaseModel):
    # 最近几轮对话（通常是刚结束的那一轮，再加上前面一两轮做上下文）
    messages: list[ChatMessage] = Field(min_length=1, max_length=20)
    memories: list[MemoryIn] = Field(default_factory=list, max_length=2000)


@router.post("/memory/extract")
async def memory_extract(
    req: ExtractRequest,
    model: ChatModel = Depends(get_model),
    embedder: Any = Depends(get_embedder),
) -> dict[str, list[dict[str, Any]]]:
    """返回记忆要做的操作：add（新增）、update（修改，id 是原来那条）、delete（删除）。由桌面端执行并同步"""
    turns = [Turn(m.role, m.content) for m in req.messages]
    memories = [m.to_memory() for m in req.memories]
    try:
        ops = await extract(model, turns, memories, embedder, datetime.now().astimezone())
    except Exception as err:  # 大模型没连上、超时：告诉桌面端这次没整理成，它过一会儿再试（以前是当成「没什么要记的」）
        log.warning("整理记忆失败：%s", err)
        code, message = classify_error(err)
        raise HTTPException(status_code=502, detail={"code": code, "message": message}) from err
    return {"ops": ops}


class FactsIn(BaseModel):
    memories: list[MemoryIn] = Field(default_factory=list, max_length=2000)


@router.put("/memory/facts")
async def memory_facts(req: FactsIn, store: MemoryStore = Depends(get_memory_store)) -> dict[str, int]:
    """桌面端的全部记忆（正本在桌面端）对齐到记忆库：记忆变了、AI 服务刚启动时推一次。删掉的在这里也删掉"""
    changed = await asyncio.to_thread(store.sync_facts, [m.to_memory() for m in req.memories])
    return {"changed": changed, "total": len(req.memories)}


@router.get("/memory/episodes")
def memory_episodes(limit: int = 100, store: MemoryStore = Depends(get_memory_store)) -> list[dict[str, Any]]:
    """她记下的经历（对话摘要、编程任务做了什么），新的在前：记忆页上给你看、给你删"""
    out = []
    for e in store.recent(EPISODE, None, max(1, min(limit, 300))):
        out.append({"id": e.id, "content": e.content, "source": e.meta.get("source", ""), "project": e.scope,
                    "at": datetime.fromtimestamp(e.updated_at).astimezone().isoformat()})
    return out


@router.delete("/memory/episodes/{entry_id}")
def memory_forget_episode(entry_id: str, store: MemoryStore = Depends(get_memory_store)) -> dict[str, bool]:
    if not store.forget_episode(entry_id):
        raise HTTPException(status_code=404, detail="没有这条")
    return {"ok": True}


@router.delete("/memory/episodes")
def memory_forget_episodes(store: MemoryStore = Depends(get_memory_store)) -> dict[str, int]:
    return {"deleted": store.forget_episodes()}
