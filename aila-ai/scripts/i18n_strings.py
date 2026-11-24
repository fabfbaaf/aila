r"""从 aila-ai 的代码里找出会显示给用户的中文：raise 出来的错误、工具的进度和确认文字。f-string 变成 {0} {1} 这样的模板。

    .venv\Scripts\python scripts\i18n_strings.py app   # 打印 JSON：模板 → 出现在哪

英文界面（app/i18n.py）的词典 app/locales_en*.py 要包含这里找出的每一条；tests/test_i18n.py 用 ui_strings() 检查。
"""
import ast
import json
import re
import sys
from pathlib import Path

CJK = re.compile(r"[一-鿿]")
DANGER_TABLES = ("DANGEROUS", "COMPUTER_DANGEROUS")


def template(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts, n = [], 0
        for v in node.values:
            if isinstance(v, ast.Constant):
                parts.append(str(v.value).replace("{", "{{").replace("}", "}}"))
            else:
                parts.append("{" + str(n) + "}")
                n += 1
        return "".join(parts)
    return None


def strings_in(node: ast.AST) -> list[str]:
    found = []
    for sub in ast.walk(node):
        if isinstance(sub, (ast.Constant, ast.JoinedStr)):
            # f-string 里面的常量片段单独不算
            t = template(sub)
            if t and CJK.search(t):
                found.append(t)
    # 去掉是别的 f-string 片段的
    return found


def ui_strings(root: Path) -> dict[str, list[str]]:
    """模板 → 出现在哪些地方（文件:行）"""
    out: dict[str, list[str]] = {}

    def add(t: str, where: str) -> None:
        t = t.strip()
        if t and CJK.search(t):
            out.setdefault(t, []).append(where)

    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel.startswith(("tests/", "scripts/", ".venv/")) or "locales" in rel:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {}
        for node in ast.walk(tree):
            for ch in ast.iter_child_nodes(node):
                parents[ch] = node
        for node in ast.walk(tree):
            # 确认卡片上「为什么要问」、工具卡片上「为什么没做」：Decision("ask"/"deny", "…")，危险命令表里每条的原因
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Decision" \
                    and len(node.args) > 1 and isinstance(node.args[0], ast.Constant) and node.args[0].value in ("ask", "deny"):
                t = template(node.args[1])
                if t:
                    add(t, f"{rel}:{node.lineno}")
            if isinstance(node, ast.Assign) and any(isinstance(n, ast.Name) and n.id in DANGER_TABLES for n in node.targets) \
                    and isinstance(node.value, ast.List):
                for item in node.value.elts:
                    if isinstance(item, ast.Tuple) and len(item.elts) > 1 and (t := template(item.elts[1])):
                        add(t, f"{rel}:{item.lineno}")
            # raise X("…") / raise X(f"…")
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args:
                for arg in node.exc.args:
                    t = template(arg)
                    if t:
                        add(t, f"{rel}:{node.lineno}")
            # 工具的进度、确认文字：CodingTool(...) / Tool(...) 里除了说明以外的 lambda 和 action=
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("CodingTool", "Tool"):
                for i, arg in enumerate(node.args):
                    if i in (0, 1, 2):  # 名字、说明（给大模型的）、参数
                        continue
                    if isinstance(arg, ast.Lambda):
                        for sub in ast.walk(arg.body):
                            t = template(sub) if isinstance(sub, (ast.Constant, ast.JoinedStr)) else None
                            if t and CJK.search(t) and not isinstance(parents.get(sub), ast.JoinedStr):
                                add(t, f"{rel}:{node.lineno}")
                for kw in node.keywords:
                    if kw.arg in ("status", "action", "detail"):
                        for sub in ast.walk(kw.value):
                            t = template(sub) if isinstance(sub, (ast.Constant, ast.JoinedStr)) else None
                            if t and CJK.search(t) and not isinstance(parents.get(sub), ast.JoinedStr):
                                add(t, f"{rel}:{node.lineno}")
    return out


if __name__ == "__main__":
    json.dump(ui_strings(Path(sys.argv[1])), sys.stdout, ensure_ascii=False, indent=1)
