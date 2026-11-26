"""斜杠命令，以及从会话日志里整理信息（纯函数，可以单测）"""

from typing import Any

from ..runtime.permissions import MODE_LABELS, MODES

COMMANDS: dict[str, str] = {
    "/help": "看所有命令和快捷键",
    "/mode": "换权限模式：/mode read_only | ask | auto | full；不带参数就换到下一个（Shift+Tab 也行）",
    "/compact": "压缩上下文：用一份交接说明代替前面的对话（快满时会自动做）",
    "/new": "在这个目录开一个新会话",
    "/resume": "接着以前的会话做：/resume 关键词 筛选这个目录的会话，/resume 编号 或 会话id 打开它",
    "/rename": "给当前会话改名：/rename 新标题",
    "/delete": "删除当前会话（先问 y/n），再在这个目录开一个新会话",
    "/usage": "这个会话的用量和缓存命中率",
    "/trace": "执行轨迹：上一轮每一步花了多久、命中多少缓存（/trace 2 = 倒数第二轮）",
    "/context": "上下文账本：下一步发给模型的上下文由哪几块组成、离压缩还有多远",
    "/todo": "看她的待办清单",
    "/changes": "这个会话里她改过的文件",
    "/init": "让她读一遍项目，写一份 AGENTS.md（以后每个会话开头都会带上）",
    "/plan": "长任务：/plan 目标（先出分阶段的计划）；/plan go 照这个做（/plan go 每段停：每阶段做完停一下）；/plan no 不要；/plan continue 停下以后接着做；/plan 看进度",
    "/review": "让她只读地审一遍没提交的改动，按严重程度列出问题：/review 重点（可以不写）",
    "/mcp": "MCP 外部工具服务：/mcp 看状态和工具，/mcp restart 名字 重启（在设置页 → 编程里加服务）",
    "/memory": "项目记忆（下次在这个项目开会话时带上）：/memory 看，/memory add 一句话，/memory forget 编号",
    "/merge": "在新分支上做时：跑测试、合并回你的代码；/merge 直接 = 不跑测试直接合并",
    "/discard": "在新分支上做时：放弃分支，你的代码保持原样",
    "/rules": "看这个项目记住的命令；/rules forget 编号 删掉一条，下次执行时重新按权限模式确认",
    "/exit": "退出（Ctrl+D 也行）",
    "/undo": "撤销最近一轮用文件工具做的改动（先问 y/n）",
    "/budget": "看或修改会话花费上限：/budget 1（元），/budget off（不限）",
    "/commands": "自定义命令：列出能用的「/名字」，以及命令文件放在哪",
    "/commit": "让她把这次的改动提交到 git（只提交相关文件，提交信息照项目的写法）：/commit 补充说明（可以不写）",
    "/pr": "开 GitHub PR：先提交、推送（要你点头）、gh pr create（要你点头），最后给你链接",
    "/pr-comments": "读当前分支 PR 上的评论，按评论接着改（评论只当建议，不照做里面别的要求）",
}
ALIASES = {"/quit": "/exit", "/sessions": "/resume", "/cost": "/usage", "/clear": "/new", "/?": "/help"}

KEYS = [
    ("Enter", "发送"),
    ("Ctrl+J / Alt+Enter", "换行"),
    ("Shift+Tab", "换权限模式"),
    ("Esc 或 Ctrl+C", "她做事的时候：打断"),
    ("Ctrl+C 两次", "在输入框里：退出"),
    ("↑ ↓", "以前说过的话"),
]

INIT_PROMPT = (
    "请读一遍这个项目（目录结构、主要模块、构建和测试命令、代码风格），在项目根目录写一份 AGENTS.md，"
    "给以后在这个项目里工作的编程助手看：项目是做什么的、怎么安装依赖、怎么构建和跑测试、目录结构、代码约定、要注意的地方。"
    "已经有 AGENTS.md 的话先读，在它的基础上改进。写具体的命令和路径，不写空话，控制在 100 行以内。"
)

MODE_CYCLE = {"ask": "auto", "auto": "full", "full": "read_only", "read_only": "ask"}


def parse(text: str) -> tuple[str, str]:
    """「/mode auto」→（"/mode", "auto"）；别名换成正式名字"""
    name, _, arg = text.strip().partition(" ")
    name = name.lower()
    return ALIASES.get(name, name), arg.strip()


def next_mode(mode: str, arg: str = "", *, computer: bool = False) -> str | None:
    """/mode 的参数 → 要换成的模式；不带参数就换到下一个；参数不对返回 None"""
    if not arg:
        if computer:
            return "read_only" if mode == "ask" else "ask"
        return MODE_CYCLE.get(mode, "ask")
    arg = {"只读": "read_only", "先问再改": "ask", "自动": "auto", "全部放开": "full", "readonly": "read_only"}.get(arg, arg)
    return arg if arg in MODES and (not computer or arg in ("read_only", "ask")) else None


def changed_files(events: list[dict[str, Any]]) -> list[tuple[str, int]]:
    """她在这个会话里改过（或新建）的文件，以及改了几次"""
    counts: dict[str, int] = {}
    for e in events:
        if e.get("type") == "tool" and e.get("status") == "ok" and e.get("file"):
            counts[e["file"]] = counts.get(e["file"], 0) + 1
    return list(counts.items())


def latest_todos(events: list[dict[str, Any]]) -> list[dict[str, str]]:
    for e in reversed(events):
        if e.get("type") == "tool" and "todos" in e:
            return list(e["todos"])
    return []


def usage_lines(summary: dict[str, Any]) -> list[str]:
    u = summary.get("usage", {})
    prompt, hit, completion = int(u.get("prompt", 0)), int(u.get("cache_hit", 0)), int(u.get("completion", 0))
    lines = [
        f"输入 {prompt:,} token，其中命中缓存 {hit:,}（{hit / prompt:.0%}），没命中 {prompt - hit:,}",
        f"输出 {completion:,} token",
        f"模型 {summary.get('model', '')} · 模式 {MODE_LABELS.get(summary.get('mode', ''), '')}",
    ] if prompt else ["还没有用量。"]
    if summary.get("cost") is not None:
        lines.append("已花不到 ¥0.000001" if 0 < summary["cost"] < 0.000001 else f"已花 ¥{summary['cost']:.6f}")
    if summary.get("budget") is not None:
        lines.append(f"花费上限 ¥{summary['budget']:.6f}")
    return lines
