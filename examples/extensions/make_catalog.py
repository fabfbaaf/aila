r"""把这里的几个示例扩展打成 zip，再生成一份本机目录 catalog.json（试「扩展目录」用）。

用法（在仓库根目录）：
    aila-ai\.venv\Scripts\python examples\extensions\make_catalog.py

产物在 examples/extensions/dist/（不进 Git）。然后在设置 →「扩展」→「目录」里加上
dist\catalog.json 的完整路径，就能在目录里看到这几个扩展、点「看看」再装。
放到网上的话，把 dist 里的文件传到一个 https 地址，目录地址填那个 catalog.json 的网址。
"""

import hashlib
import json
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIST = HERE / "dist"


def main() -> int:
    DIST.mkdir(exist_ok=True)
    entries = []
    for folder in sorted(p for p in HERE.iterdir() if (p / "aila-extension.json").is_file()):
        manifest = json.loads((folder / "aila-extension.json").read_text(encoding="utf-8"))
        name = f"{manifest['id']}-{manifest['version']}.zip"
        target = DIST / name
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(folder.rglob("*")):
                if f.is_file() and "__pycache__" not in f.parts:
                    z.write(f, f.relative_to(folder).as_posix())
        data = target.read_bytes()
        entries.append({
            "id": manifest["id"], "name": manifest.get("name", manifest["id"]), "version": manifest["version"],
            "description": manifest.get("description", ""), "url": name,
            "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
        })
        print(f"{name}  {len(data)} 字节")
    catalog = {"name": "示例扩展", "extensions": entries}
    (DIST / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"目录：{DIST / 'catalog.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
