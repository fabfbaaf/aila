"""「使用说明」技能包：她知道自己的所有功能，能带你去对应的页面，还能在自己的源码上开任务改自己。

为什么要有：功能多了，用户问「这个在哪设置」「你能不能……」时，大模型凭印象答常常答错（说没有的功能、指错地方）。
所以写一份使用说明（guide.md，和代码一起维护，桌面端的测试会检查每个页面、设置分区都写到了），
她回答前先按问题查相关的几节，照着说。

三个工具：
- aila_guide：按问题查使用说明，返回最相关的几节（按字词重合打分，标题算得重；不用向量模型，打包版也一样能用）
- open_page：让主窗口切到某一页（页面、设置分区只认固定的几个，桌面端收到后再用 shell.ts 的规则检查一遍）
- improve_aila：从源码运行时，在艾拉自己的仓库里开编程任务，而且一定在新分支上做（git worktree）：
  她改的是自己，你看过 diff、合并了才生效。装的是安装包（没有源码、不是 git 仓库）时告诉你去哪里克隆
"""

import re
import sys
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

GUIDE = Path(__file__).with_name("guide.md")
# 主窗口的页面、设置分区（和 aila-desktop/src/shared/shell.ts 对得上，桌面端测试会检查）
PAGES = ("tasks", "chat", "projects", "knowledge", "memory", "studio", "settings")
SECTIONS = ("model", "voice", "desktop", "extensions", "phone", "account", "appearance", "status", "agent")
REPO_URL = "https://github.com/fabfbaaf/aila"
TOP = 3


@dataclass(frozen=True)
class Section:
    title: str
    body: str

    @property
    def text(self) -> str:
        return f"## {self.title}\n{self.body.strip()}"


@cache
def sections() -> tuple[Section, ...]:
    """按「## 标题」切成几节（第一个标题之前的说明不算）"""
    raw = GUIDE.read_text(encoding="utf-8")
    parts = re.split(r"^## +(.+)$", raw, flags=re.M)
    return tuple(Section(parts[i].strip(), parts[i + 1]) for i in range(1, len(parts) - 1, 2))


def _grams(text: str) -> set[str]:
    """中文按相邻两个字、英文和数字按整词：「语音模型」→ 语音、音模、模型"""
    text = text.lower()
    words = set(re.findall(r"[a-z0-9_+./-]{2,}", text))
    for run in re.findall(r"[一-鿿]+", text):
        words |= {run[i : i + 2] for i in range(len(run) - 1)} if len(run) > 1 else {run}
    return words


# 问题里常见、但不说明在问什么的词：不参与打分
STOP = _grams("怎么怎样如何哪里在哪什么可以能不能一下这个那个你的我的艾拉她你我吗呢吧啊设置功能")


def search(question: str, top: int = TOP) -> list[Section]:
    """最相关的几节；一节都对不上时给「艾拉是什么」和「第一次用」"""
    want = _grams(question) - STOP
    scored = []
    for i, s in enumerate(sections()):
        title, body = _grams(s.title), _grams(s.body)
        score = 3 * len(want & title) + len(want & body)
        if score:
            scored.append((score, -i, s))
    scored.sort(reverse=True)
    found = [s for _, _, s in scored[:top]]
    return found or list(sections()[:2])


def source_repo(root: Path | None = None) -> Path | None:
    """艾拉自己的源码仓库（从源码运行时）：aila-ai 的上一层，里面有 aila-desktop 和 .git。打包版没有"""
    if getattr(sys, "frozen", False):
        return None
    repo = (root or Path(__file__).resolve().parents[3]).resolve()
    if (repo / "aila-ai").is_dir() and (repo / "aila-desktop").is_dir() and (repo / ".git").exists():
        return repo
    return None


def page_route(args: dict[str, Any]) -> dict[str, str] | str:
    """open_page 的参数 → 主窗口的页面地址；不认识的返回一句说明"""
    page = str(args.get("page", "")).strip()
    if page not in PAGES:
        return f"没有「{page}」这一页。能去的：{'、'.join(PAGES)}。"
    route = {"page": page}
    section = str(args.get("section", "")).strip()
    if page == "settings":
        if section and section not in SECTIONS:
            return f"设置里没有「{section}」这个分区。有：{'、'.join(SECTIONS)}。"
        route["section"] = section or "model"
    if page == "projects":
        route["tab"] = "map" if str(args.get("tab", "")).strip() == "map" else "list"
    return route
