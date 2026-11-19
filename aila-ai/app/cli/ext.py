"""终端里管扩展包：aila ext list / install <zip 或文件夹> / remove <id> / enable <id> / disable <id>

AI 服务开着时走它的接口（服务会跟着停掉、补开扩展的工具服务，桌面上也马上看到）；
没开就直接改 <数据目录>/extensions。装之前一样先列出代码算出来的清单，你按 y 才装。
"""

import argparse
import sys
from pathlib import Path
from typing import Any

import httpx

from ..extensions import ExtensionError, ExtensionStore


class _Remote:
    def __init__(self, url: str) -> None:
        self.client = httpx.Client(base_url=url, timeout=httpx.Timeout(60.0), trust_env=False)

    def _call(self, method: str, path: str, body: Any = None) -> Any:
        res = self.client.request(method, path, json=body)
        if res.status_code >= 400:
            try:
                detail = res.json().get("detail")
            except ValueError:
                detail = None
            raise ExtensionError(str(detail or f"AI 服务返回 {res.status_code}"))
        return res.json()

    def listing(self) -> list[dict]:
        return self._call("GET", "/extensions")["extensions"]

    def stage(self, path: Path) -> tuple[str, dict]:
        res = self._call("POST", "/extensions/inspect", {"path": str(path.resolve())})
        return res["token"], res["plan"]

    def install(self, token: str) -> None:
        self._call("POST", "/extensions/install", {"token": token})

    def discard(self, token: str) -> None:
        self._call("POST", "/extensions/discard", {"token": token})

    def remove(self, ext_id: str) -> None:
        self._call("DELETE", f"/extensions/{ext_id}")

    def set_enabled(self, ext_id: str, enabled: bool) -> None:
        self._call("PATCH", f"/extensions/{ext_id}", {"enabled": enabled})


class _Local:
    def __init__(self, store: ExtensionStore) -> None:
        self.store = store

    def listing(self) -> list[dict]:
        return [self.store.describe(e) for e in self.store.installed()]

    def stage(self, path: Path) -> tuple[str, dict]:
        return self.store.stage(path)

    def install(self, token: str) -> None:
        self.store.install(token)

    def discard(self, token: str) -> None:
        self.store.discard(token)

    def remove(self, ext_id: str) -> None:
        self.store.uninstall(ext_id)

    def set_enabled(self, ext_id: str, enabled: bool) -> None:
        self.store.set_enabled(ext_id, enabled)


def _backend() -> "_Remote | _Local":
    from ..deps import settings
    from .backend import service_url

    url = service_url()
    try:
        if httpx.get(url + "/extensions", timeout=1.5, trust_env=False).status_code == 200:
            return _Remote(url)
    except httpx.HTTPError:
        pass
    return _Local(ExtensionStore(settings.data_dir / "extensions"))


def format_plan(plan: dict) -> str:
    """装之前的清单（和设置页显示的是同一份数据）"""
    lines = [f"{plan['name']}（{plan['id']}）{plan['version']}", plan["description"] or "", ""]
    if plan["commands"]:
        lines.append("加这些命令：" + "、".join("/" + c for c in plan["commands"]))
    for s in plan["skills"]:
        lines.append(f"加一项本事：{s['name']}：{s['description']}")
    for s in plan["servers"]:
        lines.append(f"会运行：{s['command']}" + ("（只读工具）" if s["readOnly"] else ""))
    for s in plan["settings"]:
        lines.append(f"要填的设置：{s['label']}" + ("（密钥）" if s["secret"] else "") + ("，必填" if s["required"] else ""))
    if not (plan["commands"] or plan["skills"] or plan["servers"]):
        lines.append("（里面什么都没有）")
    for w in plan["warnings"]:
        lines.append("注意：" + w)
    return "\n".join(line for line in lines)


def main(argv: list[str], ask=input, out=print, backend: "_Remote | _Local | None" = None) -> int:
    p = argparse.ArgumentParser(prog="aila ext", description="管扩展包")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="装了哪些")
    inst = sub.add_parser("install", help="装一个扩展包（zip 或文件夹）")
    inst.add_argument("path")
    inst.add_argument("-y", "--yes", action="store_true", help="不问，直接装")
    for name, text in [("remove", "卸载"), ("enable", "启用"), ("disable", "停用")]:
        sub.add_parser(name, help=text).add_argument("id")
    args = p.parse_args(argv)
    backend = backend or _backend()
    try:
        if args.cmd == "list":
            items = backend.listing()
            if not items:
                out("还没装扩展。用 aila ext install <zip 或文件夹> 装一个。")
            for e in items:
                state = "启用" if e["enabled"] else "停用"
                out(f"{e['id']}  {e['name']} {e['version']}（{state}）  命令 {len(e['commands'])} · 本事 {len(e['skills'])} · 工具服务 {len(e['servers'])}")
        elif args.cmd == "install":
            token, plan = backend.stage(Path(args.path))
            out(format_plan(plan))
            if not args.yes and ask("装吗？[y/N] ").strip().lower() not in ("y", "yes", "是"):
                backend.discard(token)
                out("没装。")
                return 0
            backend.install(token)
            out(f"装好了。{'它的工具服务停着，到设置里的「扩展」打开。' if plan['servers'] else ''}新开的会话里就能用。")
        elif args.cmd == "remove":
            backend.remove(args.id)
            out(f"卸载了 {args.id}。")
        else:
            backend.set_enabled(args.id, args.cmd == "enable")
            out(f"{'启用' if args.cmd == 'enable' else '停用'}了 {args.id}。")
    except (ExtensionError, OSError, httpx.HTTPError) as err:
        out(f"没成：{err}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
