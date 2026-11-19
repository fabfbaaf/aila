"""read_skill：读扩展包里一项「本事」的全文。

会话开头的环境说明里只列了本事的名字和一句话简介（省上下文），用得上时她调这个工具读全文，
或者读那项本事文件夹里附带的文件（例子、模板）。只读、不出那个文件夹，所以不用问。
本事是你装的扩展给她的做事说明；她照着做时，改文件、跑命令照样按权限来问你。
"""

from typing import Any

from ..extensions import ExtensionError, ExtensionStore, read_skill_file


def read_skill_tool(store: ExtensionStore, names: list[str]):  # -> CodingTool
    from .tools import CodingTool, Outcome, _obj, _str

    async def run(args: dict[str, Any]) -> Outcome:
        name = str(args.get("name", "")).strip()
        # 只认会话开头列出来的（之后新装的要开新会话才有）
        if name.lower() not in {n.lower() for n in names}:
            return Outcome(f"没有叫「{name}」的本事。能用的：{'、'.join(names)}", ok=False)
        skill = next((s for s in store.skills() if s.name.lower() == name.lower()), None)
        if skill is None:
            return Outcome(f"「{name}」所在的扩展已经停用或卸载了。", ok=False)
        try:
            text = read_skill_file(skill, str(args.get("file", "") or ""))
        except ExtensionError as err:
            return Outcome(str(err), ok=False)
        return Outcome(f"【{skill.name}】（来自扩展「{skill.extension_name}」）\n{text}")

    return CodingTool(
        "read_skill",
        "读扩展带来的一项本事的全文（会话开头「扩展的本事」里列出的那些）。"
        "做的事用得上哪一项，先读它再照着做；file 可以读这项本事文件夹里附带的文件（比如例子、模板），不填就是正文。",
        _obj({"name": _str("本事的名字"), "file": _str("可选：本事文件夹里的文件，相对路径")}, ["name"]),
        run, lambda _: "read", lambda a: f"读本事「{a.get('name', '')}」",
    )
