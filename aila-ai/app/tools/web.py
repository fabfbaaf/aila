"""「联网」技能包：联网搜索、读网页。都是只读的。"""

from typing import Any
from urllib.parse import urlsplit

import httpx

from . import UNTRUSTED_NOTE, Skill, Tool, ToolError, Toolbox
from .reader import PageError, Resolver, fetch_page, make_resolver
from .search import SearchProvider

SEARCH_COUNT = 5
SNIPPET_CHARS = 300


def web_skill(client: httpx.AsyncClient, search: SearchProvider | None, resolve: Resolver | None = None) -> Skill:
    resolve = resolve or make_resolver(client)

    async def web_search(args: dict[str, Any]) -> str:
        assert search is not None
        query = str(args.get("query", "")).strip()
        if not query:
            return "搜索关键词是空的。"
        try:
            results = await search.search(query, SEARCH_COUNT)
        except httpx.HTTPStatusError as err:
            raise ToolError(f"请求失败（HTTP {err.response.status_code}）") from err
        except httpx.HTTPError as err:
            raise ToolError(f"网络出错：{type(err).__name__}") from err
        if not results:
            return f"搜索「{query}」没有找到结果。"
        lines = [f"搜索「{query}」的结果。{UNTRUSTED_NOTE}"]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r.title}\n   {r.url}\n   {r.snippet[:SNIPPET_CHARS]}")
        return "\n".join(lines)

    async def read_page(args: dict[str, Any]) -> str:
        try:
            page = await fetch_page(str(args.get("url", "")).strip(), client, resolve)
        except PageError as err:
            raise ToolError(f"读取失败：{err}") from err
        except httpx.HTTPError as err:
            raise ToolError(f"网络出错：{type(err).__name__}") from err
        tail = "\n（正文太长，只给了前面一部分）" if page.truncated else ""
        return f"网页标题：{page.title or '（无）'}\n网址：{page.url}\n{UNTRUSTED_NOTE}\n\n{page.text}{tail}"

    tools = [
        Tool(
            "read_page",
            "读取一个网页的正文。用户给了链接，或者搜索结果的摘要不够详细时使用。",
            {"type": "object", "properties": {"url": {"type": "string", "description": "完整网址，以 http:// 或 https:// 开头"}}, "required": ["url"]},
            read_page,
            lambda a: f"正在阅读 {urlsplit(str(a.get('url', ''))).hostname or '网页'}",
        )
    ]
    if search is not None:
        # 没配置搜索时只给读网页，免得大模型调一个用不了的工具
        tools.insert(
            0,
            Tool(
                "web_search",
                "联网搜索。用户问到新闻、价格、版本号、近期发生的事，或者你不确定的事实时使用。",
                {"type": "object", "properties": {"query": {"type": "string", "description": "搜索关键词，简短具体"}}, "required": ["query"]},
                web_search,
                lambda a: f"正在搜索「{a.get('query', '')}」",
            ),
        )
    return Skill("web", "联网", "搜索网页、读你给的链接", tools)


def web_toolbox(client: httpx.AsyncClient, search: SearchProvider | None, resolve: Resolver | None = None) -> Toolbox:
    """只有联网技能的工具箱（测试和简单场景用）"""
    return Toolbox(web_skill(client, search, resolve).tools)
