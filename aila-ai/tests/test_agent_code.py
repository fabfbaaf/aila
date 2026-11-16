"""编程 Agent 用代码库：按意思搜、按报错定位、调用关系；没登记的项目第一次用时自动登记"""

import json
from pathlib import Path

import pytest

from app.code import CodeProjects
from app.llm import ToolCall
from app.runtime.codetools import CodeAccess
from app.runtime.runner import Runtime

from .test_runtime import SHELL, Scripted, drive

CART = '''"""购物车"""


def item_total(price, qty):
    """一件商品的小计"""
    return price * qty


def checkout(items):
    """结账：算总价"""
    total = 0
    for price, qty in items:
        total += item_total(price, qty)
    return total
'''

TRACE = '''Traceback (most recent call last):
  File "{root}/shop/cart.py", line 14, in checkout
    total += item_total(price, qty)
  File "{root}/shop/cart.py", line 6, in item_total
    return price * qty
TypeError: can't multiply sequence by non-int of type 'str'
'''


@pytest.fixture
def shop(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "shop").mkdir(parents=True)
    (root / "shop" / "cart.py").write_text(CART, encoding="utf-8")
    return root


@pytest.mark.anyio
async def test_search_symbol_and_locate(shop: Path, tmp_path: Path) -> None:
    projects = CodeProjects(tmp_path / "data", lambda: None)
    access = CodeAccess(projects, shop)
    tools = {t.name: t for t in access.tools()}

    found = await tools["code_search"].run({"query": "checkout"})
    assert "第一次用" in found.content and "shop/cart.py:" in found.content
    assert [p.root for p in projects.all()] == [str(shop.resolve())]  # 自动登记了

    sym = await tools["code_symbol"].run({"name": "item_total"})
    assert "调用它的：checkout" in sym.content and "第一次用" not in sym.content

    loc = await tools["code_locate"].run({"error": TRACE.format(root=shop.as_posix())})
    assert "item_total" in loc.content and "→" in loc.content and "调用它的：checkout" in loc.content


@pytest.mark.anyio
async def test_workspace_inside_a_registered_project(shop: Path, tmp_path: Path) -> None:
    projects = CodeProjects(tmp_path / "data", lambda: None)
    projects.add(str(shop))
    access = CodeAccess(projects, shop / "shop")  # 在子目录里工作
    found = await {t.name: t for t in access.tools()}["code_search"].run({"query": "checkout"})
    assert "第一次用" not in found.content
    assert "\n[1] cart.py:" in found.content  # 路径换成相对工作区的
    assert len(projects.all()) == 1


@pytest.mark.anyio
async def test_agent_gets_code_tools(shop: Path, tmp_path: Path) -> None:
    model = Scripted([[ToolCall("s1", "code_symbol", json.dumps({"name": "checkout"}))], ["checkout 调用了 item_total。"]])
    projects = CodeProjects(tmp_path / "data", lambda: None)
    rt = Runtime(tmp_path / "data", lambda: model, shell=SHELL, code_projects=lambda: projects)
    sid = rt.create(str(shop)).id
    agent = rt.agent(sid)
    names = [t["function"]["name"] for t in agent.tools.specs]
    assert names[-3:] == ["code_search", "code_locate", "code_symbol"]  # 接在基本工具后面，顺序固定
    events = await drive(agent, "checkout 调用了谁")
    result = next(e for e in events if e["type"] == "tool")
    assert result["status"] == "ok" and "它调用的：item_total" in result["content"]
