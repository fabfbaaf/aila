"""编程 Agent 用代码库（代码 RAG + 调用图）：按意思搜函数、按报错定位、看一个函数的调用关系。

比纯文本搜索多的东西：
- 按函数切块的混合检索（名字、关键词、向量），找「下单在哪处理」这种不知道确切名字的代码
- 报错的调用栈直接对到函数，带上谁调用了它、它调用了谁
- 调用图：一个函数被谁调用、调用了谁（改函数签名前先看看会影响哪些地方）

工作区对上代码库里登记的项目；还没登记的，第一次用时自动登记（索引只在本机），工具结果里会说一声。
索引建在用户原来的项目上（在新分支上做时也一样），所以给出的行号是原来的代码；改之前用 read_file 读现在的样子。
"""

import asyncio
from pathlib import Path
from typing import Any

from ..code import CodeChunk, CodeIndex, CodeProjects
from ..code.projects import Project
from ..tools.code import MAX_LINES, PREVIEW_LINES, _label, _neighbors, _numbered
from .tools import CodingTool, Outcome, _obj, _str

SEARCH_HITS = 6


class CodeAccess:
    def __init__(self, projects: CodeProjects, root: Path) -> None:
        self.projects = projects
        self.root = root.resolve()
        self._registered_now = False

    def project(self) -> tuple[Project, str]:
        """工作区所在的项目，以及工作区在项目里的相对路径（就是项目根目录时为空）"""
        best: tuple[Project, str] | None = None
        for p in self.projects.all():
            proot = Path(p.root).resolve()
            if self.root == proot or self.root.is_relative_to(proot):
                prefix = self.root.relative_to(proot).as_posix() if self.root != proot else ""
                if best is None or len(prefix) < len(best[1]):
                    best = (p, prefix)
        if best:
            return best
        self._registered_now = True
        return self.projects.add(str(self.root)), ""

    def index(self) -> tuple[Project, CodeIndex, str]:
        project, prefix = self.project()
        return project, self.projects.fresh(project.id), prefix

    def note(self) -> str:
        if not self._registered_now:
            return ""
        self._registered_now = False
        return ("（第一次用：已经把这个项目登记进代码库，在本机建了索引。按意思搜要等向量在后台算完，"
                "在那之前先按名字和关键词搜。）\n")

    def rel(self, file: str, prefix: str) -> str:
        """项目里的路径 → 工作区里的路径（工作区就是项目根目录时不变）"""
        if prefix and file.startswith(prefix + "/"):
            return file[len(prefix) + 1 :]
        return file

    # ---------- 工具 ----------

    def tools(self) -> list[CodingTool]:
        return [
            CodingTool(
                "code_search",
                "按意思搜代码（按函数和类）。不知道确切名字时用，比如「下单在哪处理」「登录校验」；"
                "知道名字或确切文字时用 search 更快。返回位置、说明和开头几行。",
                _obj({"query": _str("要找什么（中文或英文都行）")}, ["query"]),
                self._search, lambda _: "read", lambda a: f"按意思搜「{str(a.get('query', ''))[:30]}」",
            ),
            CodingTool(
                "code_locate",
                "有报错和调用栈时先用它：定位到项目里出错的函数，给出那段代码（出错行标 →）、谁调用了它、它调用了谁。",
                _obj({"error": _str("完整的报错文字，带调用栈最好")}, ["error"]),
                self._locate, lambda _: "read", lambda _: "按报错定位代码",
            ),
            CodingTool(
                "code_symbol",
                "看一个函数、方法或类：定义在哪、代码、谁调用它、它调用了谁（调用图）。改函数签名前先看会影响哪些地方。",
                _obj({"name": _str("名字，比如 checkout 或 CartService.total")}, ["name"]),
                self._symbol, lambda _: "read", lambda a: f"看 {str(a.get('name', ''))[:40]} 的调用关系",
            ),
        ]

    def _where(self, c: CodeChunk, prefix: str) -> str:
        return f"{self.rel(c.file, prefix)}:{c.start}-{c.end}"

    async def _search(self, args: dict[str, Any]) -> Outcome:
        query = str(args.get("query", "")).strip()
        if not query:
            return Outcome("要给搜索的内容。", ok=False)
        _, index, prefix = await asyncio.to_thread(self.index)
        hits = await asyncio.to_thread(index.search, query, SEARCH_HITS)
        if not hits:
            return Outcome(f"{self.note()}没找到和「{query}」相关的代码。可以换个说法，或者用 search 按文字搜。")
        out = [f"{self.note()}和「{query}」最相关的代码："]
        for n, hit in enumerate(hits, 1):
            out.append(f"\n[{n}] {self._where(hit, prefix)}  {_label(hit)}")
            if hit.doc:
                out.append(f"    说明：{hit.doc[:120]}")
            out.append(_numbered(index, hit, PREVIEW_LINES))
        return Outcome("\n".join(out))

    async def _locate(self, args: dict[str, Any]) -> Outcome:
        text = str(args.get("error", "")).strip()
        if not text:
            return Outcome("要给报错的内容（最好带调用栈）。", ok=False)
        _, index, prefix = await asyncio.to_thread(self.index)
        located = await asyncio.to_thread(index.locate, text)
        inside = [loc for loc in located if loc.chunk]
        if not inside:
            if not located:
                return Outcome(f"{self.note()}这段报错里没认出调用栈（文件和行号）。可以用 search 搜报错里的函数名、关键词。")
            outside = ", ".join(f"{loc.frame.file}:{loc.frame.line}" for loc in located[:4])
            return Outcome(f"{self.note()}调用栈里的位置都不在这个项目里（{outside}），可能是第三方库里的代码。")
        out = [f"{self.note()}调用栈从出错的地方往外："]
        for n, loc in enumerate(inside[:3], 1):
            assert loc.chunk is not None
            out.append(f"\n第 {n} 处：{self.rel(loc.file, prefix)}:{loc.frame.line}，在 {_label(loc.chunk)} 里")
            out.append(_numbered(index, loc.chunk, MAX_LINES, mark=loc.frame.line))
            if n == 1:
                out.extend(await asyncio.to_thread(_neighbors, index, loc))
        return Outcome("\n".join(out))

    async def _symbol(self, args: dict[str, Any]) -> Outcome:
        name = str(args.get("name", "")).strip()
        if not name:
            return Outcome("要给函数或类的名字。", ok=False)
        _, index, prefix = await asyncio.to_thread(self.index)
        chunks = await asyncio.to_thread(index.symbol, name)
        if not chunks:
            return Outcome(f"{self.note()}没找到叫「{name}」的函数或类。可以用 code_search 按意思搜。")
        out = [self.note().rstrip()] if self._registered_now else []
        for c in chunks[:3]:
            out.append(f"{self._where(c, prefix)}  {_label(c)}")
            out.append(_numbered(index, c, MAX_LINES))
            callers = await asyncio.to_thread(index.callers, c)
            callees = await asyncio.to_thread(index.callees, c)
            out.append("调用它的：" + ("；".join(f"{x.qname}（{self._where(x, prefix)}）" for x in callers) or "（没找到）"))
            out.append("它调用的：" + ("；".join(f"{x.qname}（{self._where(x, prefix)}）" for x in callees) or "（没找到）"))
            out.append("")
        if len(chunks) > 3:
            out.append(f"（还有 {len(chunks) - 3} 个同名的，名字写全一点，比如 类名.方法名）")
        return Outcome("\n".join(line for line in out if line is not None).strip())
