"""个人知识库：上传文件，她回答时可以查。文件只存在这台电脑上。"""

from typing import Any

from ..tools import UNTRUSTED_NOTE, Skill, Tool
from .parse import SUPPORTED, DocumentError
from .store import Document, Hit, KnowledgeBase

SNIPPET_CHARS = 600

__all__ = ["SUPPORTED", "Document", "DocumentError", "Hit", "KnowledgeBase", "knowledge_skill"]


def knowledge_skill(kb: KnowledgeBase) -> Skill:
    async def search_knowledge(args: dict[str, Any]) -> str:
        import asyncio

        query = str(args.get("query", "")).strip()
        hits = await asyncio.to_thread(kb.search, query)
        if not hits:
            return f"在用户的资料里没有找到和「{query}」相关的内容。"
        lines = [f"在用户的资料里搜「{query}」的结果。{UNTRUSTED_NOTE}回答时说明出自哪份资料。"]
        for i, h in enumerate(hits, 1):
            text = h.text if len(h.text) <= SNIPPET_CHARS else h.text[:SNIPPET_CHARS] + "……"
            lines.append(f"{i}. {h.source}\n{text}")
        return "\n\n".join(lines)

    tool = Tool(
        "search_knowledge",
        "在用户上传的资料（文档、笔记、PDF）里查找内容。用户问到他的文档、资料、笔记里的东西，或者问题可能在资料里有答案时使用。",
        {"type": "object", "properties": {"query": {"type": "string", "description": "要找的内容，用关键词或一句话描述"}}, "required": ["query"]},
        search_knowledge,
        lambda a: f"正在翻资料「{a.get('query', '')}」",
    )
    return Skill("knowledge", "资料库", "在你上传的文档里找答案", [tool], available=lambda: not kb.empty)
