"""「代码库」技能包：在用户登记的项目里找代码、按报错定位、读一段代码（只读），以及修代码（要确认）。

工具返回的代码都标上文件和行号，报错那一行标「→」，她回答时能说出「在 agent.py 第 41 行」。
用之前顺手做一次增量更新（只看改过的文件），查到的是用户现在的代码。

修代码（code_fix）：确认时给用户看的 diff 由代码按真实文件算出来；用户同意后在新分支上改、跑项目自带的测试，
全过才改到用户的代码里，失败自动撤回（见 app/code/fix.py）。
"""

import asyncio
import re
from pathlib import Path
from typing import Any

from ..code import CodeChunk, CodeIndex, CodeProjects, Located
from ..code.fix import Edit, FixError, FixResult, Plan, TestCommand, detect_tests, plan_edits
from . import UNTRUSTED_NOTE, Skill, Tool

MAX_LINES = 120  # 一块代码最多给这么多行
PREVIEW_LINES = 8  # 搜索结果每条预览几行


def code_skill(projects: CodeProjects) -> Skill:
    async def indexes() -> list[tuple[Any, CodeIndex]]:
        return await asyncio.to_thread(projects.fresh_indexes)

    def pick(found: list[tuple[Any, CodeIndex]], name: str | None) -> list[tuple[Any, CodeIndex]]:
        if not name:
            return found
        chosen = [(p, i) for p, i in found if p.name.lower() == name.lower()]
        return chosen or found

    async def search(args: dict[str, Any]) -> str:
        query = str(args.get("query", "")).strip()
        if not query:
            return "要给搜索的内容。"
        found = pick(await indexes(), args.get("project"))
        lines = [f"在代码库里搜「{query}」的结果（文件:行号）。{UNTRUSTED_NOTE}"]
        n = 0
        for project, index in found:
            for hit in await asyncio.to_thread(index.search, query, 5):
                n += 1
                lines.append(f"\n[{n}] {_where(project, len(found), hit)}  {_label(hit)}")
                if hit.doc:
                    lines.append(f"    说明：{hit.doc[:120]}")
                lines.append(_numbered(index, hit, PREVIEW_LINES))
        return "\n".join(lines) if n else f"代码库里没找到和「{query}」相关的代码。"

    async def locate(args: dict[str, Any]) -> str:
        text = str(args.get("error", "")).strip()
        if not text:
            return "要给报错的内容（最好带调用栈）。"
        found = await indexes()
        results = [(p, i, await asyncio.to_thread(i.locate, text)) for p, i in found]
        # 堆栈落在哪个项目里最多，就是哪个项目的报错
        project, index, located = max(results, key=lambda r: sum(1 for loc in r[2] if loc.chunk), default=(None, None, []))
        inside = [loc for loc in located if loc.chunk]
        if index is None or not inside:
            if not located:
                return "这段报错里没认出调用栈（文件和行号）。可以用 code_search 按报错信息里的函数名、关键词搜。"
            outside = ", ".join(f"{loc.frame.file}:{loc.frame.line}" for loc in located[:4])
            return f"调用栈里的位置都不在登记的项目里（{outside}），可能是第三方库里的代码。"

        out = [f"报错在「{project.name}」项目里，调用栈从出错的地方往外：{UNTRUSTED_NOTE}"]
        for n, loc in enumerate(inside[:3], 1):
            out.append(f"\n第 {n} 处：{loc.file}:{loc.frame.line}，在 {_label(loc.chunk)} 里")
            out.append(_numbered(index, loc.chunk, MAX_LINES, mark=loc.frame.line))
            if n == 1:
                out.extend(await asyncio.to_thread(_neighbors, index, loc))
        skipped = [loc for loc in located if not loc.chunk]
        if skipped:
            out.append("\n（另有 " + str(len(skipped)) + " 处在项目外，比如第三方库：" +
                       ", ".join(f"{loc.frame.file}:{loc.frame.line}" for loc in skipped[:3]) + "）")
        return "\n".join(out)

    async def read(args: dict[str, Any]) -> str:
        target = str(args.get("target", "")).strip()
        if not target:
            return "要给读哪里：文件:行号，或者函数名（比如 SyncService.putMemory）。"
        found = pick(await indexes(), args.get("project"))
        m = re.fullmatch(r"(.+?):(\d+)(?:-(\d+))?", target)
        for project, index in found:
            if m:
                chunk = await asyncio.to_thread(index.chunk_at, m.group(1).replace("\\", "/"), int(m.group(2)))
                chunks = [chunk] if chunk else []
            else:
                chunks = await asyncio.to_thread(index.symbol, target)
            if chunks:
                out = [f"{_where(project, len(found), c)}  {_label(c)}\n{_numbered(index, c, MAX_LINES)}" for c in chunks[:3]]
                return f"{UNTRUSTED_NOTE}\n" + "\n\n".join(out)
        return f"没找到「{target}」。可以先用 code_search 搜一下。"

    # ---------- 修代码 ----------

    def fix_plan(args: dict[str, Any]) -> tuple[Any, Plan, list[TestCommand]]:
        raw = args.get("edits")
        if not isinstance(raw, list) or not raw:
            raise FixError("要给出改动（edits）：每处写清文件、原文、改成什么。")
        edits = []
        for e in raw:
            if not isinstance(e, dict) or not isinstance(e.get("file"), str) or not isinstance(e.get("new", ""), str):
                raise FixError("每处改动要有 file、old、new 三项。")
            edits.append(Edit(e["file"], str(e.get("old", "")), str(e.get("new", ""))))
        candidates = pick([(p, None) for p in projects.all()], args.get("project"))  # type: ignore[list-item]
        # 第一处改动的文件在哪个项目里，就是哪个项目
        project = next((p for p, _ in candidates if (Path(p.root) / edits[0].file.replace("\\", "/")).exists()), None)
        if project is None:
            project = candidates[0][0] if len(candidates) == 1 else None
        if project is None:
            raise FixError(f"在登记的项目里找不到 {edits[0].file}。路径要相对项目根目录（和 code_locate 给的一样）。")
        plan = plan_edits(Path(project.root), edits)
        return project, plan, detect_tests(plan.repo, [c.file for c in plan.changes])

    def fix_check(args: dict[str, Any]) -> str | None:
        try:
            fix_plan(args)
        except FixError as err:
            return str(err)
        return None

    def fix_detail(args: dict[str, Any]) -> str:
        project, plan, tests = fix_plan(args)
        summary = str(args.get("summary", "")).strip()[:200]
        test_line = ("改完先在新分支上跑测试：" + "、".join(t.label for t in tests) + "，全过才改到你的代码里"
                     if tests else "没找到这个项目的测试命令：改动只留在新分支上，不会直接改你的代码")
        return "\n".join(p for p in (f"项目：{project.name}", summary, test_line, plan.diff.rstrip()) if p)

    async def fix(args: dict[str, Any]) -> str:
        try:
            _, plan, tests = await asyncio.to_thread(fix_plan, args)  # 确认之后文件可能变了：按现在的样子重新算
        except FixError as err:
            return str(err)
        result = await asyncio.to_thread(projects.fixer.run, plan, tests)
        return _fix_report(result)

    obj = "object"
    project_arg = {"type": "string", "description": "项目名（登记了好几个项目时用，不填就全部找）"}
    tools = [
        Tool(
            "code_locate",
            "用户的代码报错了（给了报错信息或调用栈）时先用它：从调用栈定位到项目里出错的函数，给出那段代码（出错行标 →）、"
            "调用它的函数和它调用的函数。",
            {"type": obj, "properties": {"error": {"type": "string", "description": "完整的报错文字，带调用栈最好"}}, "required": ["error"]},
            locate, lambda _: "正在找出错的代码",
        ),
        Tool(
            "code_search",
            "在用户登记的项目代码里搜：可以用函数名、类名，也可以用一句话描述要找的功能（「哪里检查登录」）。",
            {"type": obj, "properties": {"query": {"type": "string", "description": "函数名、关键词或者一句话描述"}, "project": project_arg},
             "required": ["query"]},
            search, lambda a: f"正在代码里找「{str(a.get('query', ''))[:20]}」",
        ),
        Tool(
            "code_read",
            "读项目里的一段代码：给「文件:行号」读包含那一行的函数，给函数名（可以带类名）读那个函数。",
            {"type": obj, "properties": {"target": {"type": "string", "description": "比如 app/agent.py:41 或 SyncService.putMemory"},
                                          "project": project_arg}, "required": ["target"]},
            read, lambda a: f"正在读 {str(a.get('target', ''))[:30]}",
        ),
        Tool(
            "code_fix",
            "修代码：给出要改的地方（文件、一字不差的原文、改成什么）。会先把 diff 给用户看，用户同意后在新分支上改、"
            "跑项目自带的测试，全部通过才改到用户的代码里，没通过自动撤回。改之前先用 code_locate / code_read 看清楚现在的代码；"
            "只改必要的几行，不要顺手重构；删除代码要格外谨慎。",
            {"type": obj, "properties": {
                "summary": {"type": "string", "description": "一句话：改了什么、为什么（给用户看）"},
                "edits": {"type": "array", "items": {"type": "object", "properties": {
                    "file": {"type": "string", "description": "文件路径，相对项目根目录，和 code_locate 给的一样"},
                    "old": {"type": "string", "description": "要换掉的原文，一字不差，带够上下文让它在文件里只出现一次；新建文件时留空"},
                    "new": {"type": "string", "description": "换成什么"},
                }, "required": ["file", "old", "new"]}},
                "project": project_arg,
            }, "required": ["summary", "edits"]},
            fix, lambda _: "正在新分支上修复并跑测试（可能要一两分钟）",
            risk="write", action="按这个改动修代码", detail=fix_detail, validate=fix_check,
        ),
    ]
    return Skill(
        "code", "代码库", "在你登记的项目里找代码、按报错定位到出错的函数；修代码时在新分支上改、测试通过才改到你的代码里",
        tools, available=lambda: bool(projects.all()),
    )


def _fix_report(r: FixResult) -> str:
    """修复结果交给大模型：测试没过时带上失败的输出，它可以据此再改一次（还会再问用户）"""
    lines = [r.reason]
    for t in r.tests:
        lines.append(f"{t.label}：{'通过' if t.ok else '没通过'}，用时 {t.seconds} 秒")
    failed = next((t for t in r.tests if not t.ok), None)
    if failed:
        lines.append(f"失败的输出（最后一段）：\n{failed.tail}")
        lines.append("可以根据失败原因再改一次（还会再问用户）；改不好就如实告诉用户。")
    return "\n".join(lines)


def _label(c: CodeChunk) -> str:
    kinds = {"function": "函数", "method": "方法", "class": "类", "module": "文件顶层代码", "template": "界面模板"}
    return f"{kinds.get(c.kind, c.kind)} {c.qname}"


def _where(project: Any, n_projects: int, c: CodeChunk) -> str:
    where = f"{c.file}:{c.start}-{c.end}"
    return f"[{project.name}] {where}" if n_projects > 1 else where


def _numbered(index: CodeIndex, c: CodeChunk, limit: int, mark: int | None = None) -> str:
    """带行号的代码（从磁盘读现在的样子）；太长时围绕出错行截一段"""
    start, end = c.start, c.end
    if end - start + 1 > limit:
        if mark is not None:
            start = max(c.start, mark - limit // 2)
            end = min(c.end, start + limit - 1)
        else:
            end = start + limit - 1
    lines = index.source(c.file, start, end)
    width = len(str(end))
    body = "\n".join(f"{'→' if n == mark else ' '} {n:>{width}} | {line}" for n, line in enumerate(lines, start))
    more = "" if end >= c.end else f"\n  …（后面还有 {c.end - end} 行）"
    return body + more


def _neighbors(index: CodeIndex, loc: Located) -> list[str]:
    """出错函数在调用图上的邻居：谁调用了它（可能传错了参数）、它调用了谁（可能是被调的出了问题）"""
    assert loc.chunk is not None
    out = []
    callers = index.callers(loc.chunk)
    callees = index.callees(loc.chunk)
    if callers:
        out.append("调用它的：" + "；".join(f"{c.qname}（{c.file}:{c.start}）" for c in callers))
    if callees:
        out.append("它调用的：" + "；".join(f"{c.qname}（{c.file}:{c.start}）{c.signature[:60]}" for c in callees))
    return out
