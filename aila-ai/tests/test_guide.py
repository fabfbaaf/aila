"""使用说明：她按问题查到对的几节、说明里写的页面地址都有效、能带你去某一页、改自己一定开在新分支上"""

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest

from app.guide import GUIDE, PAGES, SECTIONS, page_route, search, sections, source_repo
from app.runtime.runner import Runtime
from app.tools import EMIT
from app.tools.guide import guide_skill

from .test_fix import sh
from .test_runtime import SHELL, Scripted


def test_sections_and_search() -> None:
    titles = [s.title for s in sections()]
    assert len(titles) >= 15 and all(s.body.strip() for s in sections())
    assert len(titles) == len(set(titles))
    cases = {
        "语音怎么打开": "语音",
        "大模型密钥在哪填": "第一次用要做的三件事",
        "权限模式有哪几种": "四种权限模式",
        "能不能几个助手一起干活": "多智能体：几个助手一起做",
        "你能改你自己吗": "让她改自己",
        "不填密钥能联网搜索吗": "联网搜索",
        "星图里的手势": "项目和代码星图",
    }
    for question, title in cases.items():
        assert title in [s.title for s in search(question)], question
    # 一个字都对不上：给概览
    assert [s.title for s in search("zzzz")] == titles[:2]


def test_page_addresses_in_guide_are_valid() -> None:
    text = GUIDE.read_text(encoding="utf-8")
    assert set(re.findall(r"page=(\w+)", text)) <= set(PAGES)
    assert set(re.findall(r"section=(\w+)", text)) <= set(SECTIONS)
    assert set(re.findall(r"page=(\w+)", text)) == set(PAGES)  # 每一页都写到了


def test_page_route() -> None:
    assert page_route({"page": "settings", "section": "voice"}) == {"page": "settings", "section": "voice"}
    assert page_route({"page": "settings"}) == {"page": "settings", "section": "model"}
    assert page_route({"page": "projects", "tab": "map"}) == {"page": "projects", "tab": "map"}
    assert page_route({"page": "memory", "section": "voice"}) == {"page": "memory"}
    assert isinstance(page_route({"page": "admin"}), str)
    assert isinstance(page_route({"page": "settings", "section": "../x"}), str)


async def call(skill_tools: list[Any], name: str, args: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    tool = next(t for t in skill_tools if t.name == name)
    side: list[dict[str, Any]] = []
    token = EMIT.set(side.append)
    try:
        return await tool.run(args), side
    finally:
        EMIT.reset(token)


@pytest.mark.anyio
async def test_guide_and_open_page_tools(tmp_path: Path) -> None:
    tools = guide_skill(lambda: Runtime(tmp_path / "data", lambda: Scripted([["好"]]), shell=SHELL), lambda: None).tools
    assert all(t.risk == "read" for t in tools)
    text, side = await call(tools, "aila_guide", {"question": "语音怎么打开"})
    assert "## 语音" in text and "Alt+Shift+V" in text and not side
    text, side = await call(tools, "open_page", {"page": "settings", "section": "voice"})
    assert side == [{"type": "open_page", "route": {"page": "settings", "section": "voice"}}] and "设置 → 语音" in text
    text, side = await call(tools, "open_page", {"page": "nowhere"})
    assert not side and "没有" in text


def fake_aila(root: Path) -> Path:
    """长得像艾拉源码仓库的 git 仓库"""
    (root / "aila-ai").mkdir(parents=True)
    (root / "aila-desktop").mkdir()
    (root / "aila-ai" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    sh(root, "init", "-q", "-b", "main")
    sh(root, "-c", "core.autocrlf=false", "add", "-A")
    sh(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    return root


@pytest.mark.anyio
async def test_improve_aila_runs_on_a_new_branch(tmp_path: Path) -> None:
    repo = fake_aila(tmp_path / "aila")
    assert source_repo(repo) == repo and source_repo(tmp_path) is None
    rt = Runtime(tmp_path / "data", lambda: Scripted([["看过了。"]]), shell=SHELL)
    tools = guide_skill(lambda: rt, lambda: repo).tools
    text, side = await call(tools, "improve_aila", {"task": "让她说话快一点"})
    assert "新分支" in text
    sid = side[0]["session"]
    assert side[0]["type"] == "agent_task"
    session = rt.store.get(sid)
    assert session is not None and session.branch and session.branch["branch"].startswith("aila/task-")
    assert session.workdir != repo  # 改的是临时目录，源码本身不动
    for _ in range(50):
        if not rt.running(sid):
            break
        await asyncio.sleep(0.05)
    await rt.delete(sid)

    # 打包版：没有源码
    text, side = await call(guide_skill(lambda: rt, lambda: None).tools, "improve_aila", {"task": "x"})
    assert "github.com/fabfbaaf/aila" in text and not side
