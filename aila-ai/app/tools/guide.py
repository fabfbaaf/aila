"""「使用说明」技能包的工具（查使用说明、带你去某一页、改她自己），说明见 app/guide/__init__.py"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..guide import PAGES, REPO_URL, SECTIONS, page_route, search, source_repo
from ..runtime.runner import Runtime, RuntimeError_
from . import Skill, Tool, emit

PAGE_NAMES = {
    "tasks": "任务", "chat": "对话", "projects": "项目", "knowledge": "资料", "memory": "记忆", "studio": "作品", "settings": "设置",
}
SECTION_NAMES = {
    "model": "对话", "voice": "语音", "desktop": "桌面", "extensions": "技能和扩展", "phone": "手机", "account": "账号与同步",
    "appearance": "形象", "status": "运行状态", "agent": "编程",
}


def guide_skill(runtime: Callable[[], Runtime], repo: Callable[[], Path | None] = source_repo) -> Skill:
    async def ask_guide(args: dict[str, Any]) -> str:
        question = str(args.get("question", "")).strip()
        found = search(question)
        body = "\n\n".join(s.text for s in found)
        return (f"艾拉使用说明里和「{question}」相关的几节（照着回答；这里没写的功能就说没有，不要编）：\n\n{body}")

    async def open_page(args: dict[str, Any]) -> str:
        route = page_route(args)
        if isinstance(route, str):
            return route
        emit({"type": "open_page", "route": route})
        name = PAGE_NAMES[route["page"]]
        if "section" in route:
            name += f" → {SECTION_NAMES[route['section']]}"
        elif route.get("tab") == "map":
            name += " → 地图"
        return f"主窗口已经切到「{name}」。简单告诉用户在哪、要点哪里。"

    async def improve(args: dict[str, Any]) -> str:
        task = str(args.get("task", "")).strip()
        if not task:
            return "要说清楚改什么。"
        root = repo()
        if root is None:
            return (f"现在装的是安装包，没有我的源码。想让我改自己：先把 {REPO_URL} 克隆下来，"
                    "在任务页点「新任务」选那个文件夹，勾上「在新分支上做」。")
        rt = runtime()
        try:
            session = rt.create(str(root), "ask", isolate=True)
            rt.start(session.id, task)
        except RuntimeError_ as err:
            return f"没开成：{err}"
        emit({"type": "agent_task", "session": session.id, "project": root.name})
        return ("已经在任务页里开始改我自己了：在新分支上改（我的源码，不是用户的项目），改文件、跑命令前会问用户，"
                "做完要用户看过、合并以后才生效，重启艾拉后用上。简单告诉用户这些。")

    return Skill("guide", "使用说明", "问她艾拉怎么用、有什么功能，带你去对应的页面，让她改自己", [
        Tool(
            "aila_guide",
            "查艾拉（就是你自己）的使用说明。用户问你能做什么、某个功能怎么用、某个设置在哪、为什么不好用时，先查再回答，"
            "不要凭印象说；说明里没写的功能就说没有。",
            {"type": "object", "properties": {"question": {"type": "string", "description": "用户想知道的事，用他的原话"}},
             "required": ["question"]},
            ask_guide,
            lambda _: "正在翻使用说明",
        ),
        Tool(
            "open_page",
            "让主窗口切到某一页，带用户去看（比如他问某个设置在哪）。页面和设置分区按使用说明里写的 page / section 填。",
            {
                "type": "object",
                "properties": {
                    "page": {"type": "string", "enum": list(PAGES), "description": "哪一页"},
                    "section": {"type": "string", "enum": list(SECTIONS), "description": "设置页的哪个分区（只有 page=settings 时填）"},
                    "tab": {"type": "string", "enum": ["list", "map"], "description": "项目页：列表还是地图（只有 page=projects 时填）"},
                },
                "required": ["page"],
            },
            open_page,
            lambda a: f"正在打开「{PAGE_NAMES.get(str(a.get('page', '')), '')}」",
        ),
        Tool(
            "improve_aila",
            "用户想让你改进你自己（加功能、修你的 bug、改你的界面或说话方式）时用：在你自己的源码仓库里开一个编程任务，"
            "在新分支上改，用户看过、合并以后才生效。改用户自己的项目用 coding_task，不用这个。",
            {"type": "object", "properties": {"task": {"type": "string", "description": "要怎么改：用用户的原话，补上对话里提到的细节"}},
             "required": ["task"]},
            improve,
            lambda _: "正在开任务改我自己",
        ),
    ])
