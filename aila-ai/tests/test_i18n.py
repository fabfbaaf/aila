"""英文界面：发给桌面端的进度、确认、错误按词典换成英文；代码里找出来的每一条都要有英文"""

import importlib.util
from dataclasses import replace
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app import deps
from app.i18n import localize, localize_event, localize_labels, localize_summary
from app.locales_en import EN
from app.runtime.ledger import LABELS, TOOL_LABELS
from app.runtime.loop import INTERRUPTED, SKIPPED

ROOT = Path(__file__).resolve().parents[1]
HOLE = re.compile(r"\{(\d+)\}")


def load_script():
    spec = importlib.util.spec_from_file_location("i18n_strings", ROOT / "scripts" / "i18n_strings.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_every_ui_string_has_english() -> None:
    found = load_script().ui_strings(ROOT / "app")
    missing = [k for k in found if k not in EN]
    assert missing == [], f"{len(missing)} 条没有英文，比如：{missing[:5]}"
    mismatched = [k for k, v in EN.items() if sorted(HOLE.findall(k)) != sorted(HOLE.findall(v))]
    assert mismatched == []
    assert [k for k, v in EN.items() if re.search(r"[一-鿿]", v)] == []
    keys = [k for f in ("locales_en.py", "locales_en2.py", "locales_en3.py", "locales_en4.py")
            for k in re.findall(r'^    "(.+?)": ', (ROOT / "app" / f).read_text(encoding="utf-8"), re.M)]
    assert len(keys) == len(set(keys)), "有重复的键：后面的会悄悄盖掉前面的"


def test_localize_whole_sentences_and_templates(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(deps, "settings", replace(deps.settings, language="en"))
    assert localize("没有这个会话。") == "No such session."
    assert localize("读 src/cart.py") == "Read src/cart.py"
    assert localize("{a} 里找不到 old_string。要和文件一字不差（包括缩进和空格），先重新读一下那几行。".replace("{a}", "x.py")).startswith("old_string not found in x.py")
    assert localize("这是一段文件内容，不该被翻") == "这是一段文件内容，不该被翻"  # 不认识的原样
    assert localize("没有这个会话。", "zh") == "没有这个会话。"
    ev = {"type": "tool_start", "id": "c1", "name": "read_file", "status": "读 README.md"}
    assert localize_event(ev)["status"] == "Read README.md" and ev["status"] == "读 README.md"  # 日志里那份不动
    ask = localize_event({"type": "confirm", "action": "执行 rm -rf build", "detail": "rm -rf build", "reason": "会删除文件"})
    assert ask["reason"] == "Deletes files" and ask["detail"] == "rm -rf build"  # 命令本身不动
    stopped = {"type": "tool", "id": "c2", "content": "被用户打断了，没有执行。"}
    assert localize_event(stopped)["content"] == "Interrupted by the user; not run."
    todo = {"type": "tool", "id": "c4", "content": "待办清单已更新：\n[ ] 读 README"}
    assert localize_event(todo)["content"] == "To-do list updated:\n[ ] 读 README"  # 清单本身是她写的，不动
    board = {"id": "s1", "pending": {"id": "k", "action": "改 README.md"}}
    assert localize_summary(board)["pending"]["action"] == "Edit README.md" and board["pending"]["action"] == "改 README.md"
    data = {"type": "tool", "id": "c3", "content": "读 README.md"}  # 文件内容正好像模板：不翻
    assert localize_event(data) is data
    said = {"type": "assistant", "text": "没有这个会话。"}
    assert localize_event(said) is said  # 她说的话不翻


def test_http_errors_follow_language(monkeypatch) -> None:  # noqa: ANN001
    client = TestClient(__import__("app.main", fromlist=["app"]).app)
    monkeypatch.setattr(deps, "settings", replace(deps.settings, language="en"))
    assert client.get("/agent/sessions/nope").json()["detail"] == "No such session."
    monkeypatch.setattr(deps, "settings", replace(deps.settings, language="zh"))
    assert client.get("/agent/sessions/nope").json()["detail"] == "没有这个会话。"


def test_ledger_labels(monkeypatch) -> None:  # noqa: ANN001
    """上下文账本的名字是代码拼的，也要有英文；「工具结果：读文件」里填进去的那段也换掉，文件名不动"""
    assert [v for v in [*LABELS.values(), *TOOL_LABELS.values(), INTERRUPTED, SKIPPED] if v not in EN] == []
    monkeypatch.setattr(deps, "settings", replace(deps.settings, language="en"))
    rows = [{"key": "tool:read_file", "label": "工具结果：读文件", "tokens": 3}, {"label": "读文件 读文件.md"}]
    assert localize_labels(rows) == [{"key": "tool:read_file", "label": "Tool results: Read files", "tokens": 3},
                                     {"label": "Read file 读文件.md"}]
    assert rows[0]["label"] == "工具结果：读文件"


def test_skill_names_in_english(monkeypatch) -> None:  # noqa: ANN001
    """设置里、右键菜单里的技能包名字和说明：英文界面时是英文"""
    monkeypatch.setattr(deps, "settings", replace(deps.settings, language="en"))
    skills = {s["id"]: s for s in TestClient(__import__("app.main", fromlist=["app"]).app).get("/skills").json()}
    assert skills["web"]["name"] == "Web" and skills["calendar"]["name"] == "Calendar"
    assert all(not re.search(r"[一-鿿]", s["name"] + s["description"]) for s in skills.values() if not s["id"].startswith("mcp:"))
