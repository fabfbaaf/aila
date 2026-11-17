"""/review：让她只读地审一遍还没提交的改动，按严重程度列出问题。

为什么这样做：
- 改动清单（git status、diff 统计）由代码生成后放进这一轮的请求，不靠她自己去猜改了哪些文件；
  具体的 diff 她用只读命令自己看（git diff 在只读模式下直接放行），不用把整份 diff 塞进上下文
- 这一轮的权限临时当成只读（runner 负责），结束后换回原来的模式。模式变化不写进日志，
  重新加载会话时还是原来的模式
- 用户消息事件带 review 标记：界面显示成一句「审查没提交的改动」，不把整段请求摆出来
"""

import subprocess
from pathlib import Path

from ..code.fix import NO_WINDOW

# 改动清单太长就截断：文件很多时她可以自己再用 git status 看
LIST_LIMIT = 4000

REVIEW_PROMPT = """请审查这个项目里还没提交的改动。这一轮只读：不改文件，只跑查看类的命令。

怎么做：
1. 用 git diff、git diff --cached 看具体改了什么，新文件直接读；需要的话读相关的代码和测试
2. 找真正的问题：会出错的逻辑、漏掉的边界情况、安全问题、和项目约定不一致、该有却没有的测试。不挑格式和措辞
3. 按严重程度分组列出：严重 / 应该改 / 可以更好。每条写清文件和行号、问题是什么、为什么是问题、建议怎么改
4. 没发现问题就直说，不要硬凑；最后用一两句话总结能不能提交

{focus}<改动清单 生成方式="git status --short 和 git diff HEAD --stat">
{changes}
</改动清单>"""


class ReviewError(Exception):
    """没法审：不是 git 仓库、没有改动"""


def review_request(root: Path, focus: str = "") -> str:
    """这一轮发给她的请求：固定的审查说明 + 代码生成的改动清单"""
    status = _git(root, "status", "--short", "--untracked-files=all")
    if status is None:
        raise ReviewError("这个文件夹不是 git 仓库：/review 要靠 git 找出还没提交的改动。")
    if not status.strip():
        raise ReviewError("没有还没提交的改动，没什么要审的。")
    # 还没有提交过的仓库没有 HEAD：退回到暂存区的统计
    stat = _git(root, "diff", "HEAD", "--stat") or _git(root, "diff", "--cached", "--stat") or ""
    changes = status.rstrip() + (f"\n\n{stat.rstrip()}" if stat.strip() else "")
    if len(changes) > LIST_LIMIT:
        changes = changes[:LIST_LIMIT] + "\n……（太长了，后面的用 git status 自己看）"
    note = focus.strip()
    return REVIEW_PROMPT.format(focus=f"这次特别注意：{note}\n\n" if note else "", changes=changes)


def review_title(focus: str = "") -> str:
    """界面上、会话标题里显示的那一句"""
    note = focus.strip()
    return f"审查没提交的改动：{note}" if note else "审查没提交的改动"


def _git(root: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-c", "core.quotepath=false", *args],
            cwd=root,
            capture_output=True,
            timeout=15,
            creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.decode("utf-8", "replace")
