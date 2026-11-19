"""星图里的 import 关系和第三方库：项目里的连成光线，项目外的包算第三方库，标准库和 Node 自带的不算"""

from pathlib import Path

from app.code.imports import resolve, scan_imports
from app.code.index import CodeIndex

FILES = {
    "ai/app/__init__.py": "",
    "ai/app/llm.py": "import json\nimport numpy as np\nfrom fastapi import APIRouter\n",
    "ai/app/runtime/__init__.py": "",
    "ai/app/runtime/loop.py": "from ..llm import ChatModel\nfrom . import tools\nfrom app.runtime.tools import run\n",
    "ai/app/runtime/tools.py": "import os, httpx\nfrom __future__ import annotations\n",
    "web/src/main.ts": "import { createApp } from 'vue'\nimport App from './App.vue'\nimport { x } from './util.js'\nimport fs from 'node:fs'\nimport path from 'path'\nconst a = require('@scope/pkg/sub')\nexport { y } from '../shared/types'\nimport('./lazy')\n",
    "web/src/App.vue": "<script setup lang=\"ts\">\nimport { ref } from 'vue'\n</script>\n",
    "web/src/util.ts": "export const x = 1\n",
    "web/src/lazy.ts": "",
    "web/shared/types.ts": "export type y = 1\n",
    "srv/src/main/java/com/shop/Api.java": "package com.shop;\nimport com.shop.model.Item;\nimport org.springframework.web.bind.annotation.RestController;\nimport java.util.List;\nimport static org.junit.Assert.assertTrue;\n",
    "srv/src/main/java/com/shop/model/Item.java": "package com.shop.model;\n",
}
LANGS = {f: {"py": "python", "ts": "typescript", "vue": "vue", "java": "java"}[f.rsplit(".", 1)[1]] for f in FILES}


def test_scan_specs() -> None:
    assert scan_imports(FILES["ai/app/runtime/loop.py"], "python") == ["2:llm:ChatModel", "1::tools", "0:app.runtime.tools:run"]
    assert "./App.vue" in scan_imports(FILES["web/src/main.ts"], "typescript")
    assert scan_imports(FILES["srv/src/main/java/com/shop/Api.java"], "java")[-1] == "org.junit.Assert"


def test_resolve_links_and_libs() -> None:
    found = {f: scan_imports(text, LANGS[f]) for f, text in FILES.items()}
    links, libs = resolve(found, LANGS)
    assert ("ai/app/runtime/loop.py", "ai/app/llm.py") in links
    assert ("ai/app/runtime/loop.py", "ai/app/runtime/tools.py") in links
    assert ("web/src/main.ts", "web/src/App.vue") in links and ("web/src/main.ts", "web/src/util.ts") in links
    assert ("web/src/main.ts", "web/shared/types.ts") in links and ("web/src/main.ts", "web/src/lazy.ts") in links
    assert ("srv/src/main/java/com/shop/Api.java", "srv/src/main/java/com/shop/model/Item.java") in links
    names = {v["name"]: v for v in libs.values()}
    assert set(names) == {"numpy", "fastapi", "httpx", "vue", "@scope/pkg", "org.springframework", "org.junit"}
    assert names["vue"]["users"] == {"web/src/main.ts", "web/src/App.vue"}
    # 标准库、Node 自带的、项目自己的包都不算第三方库
    assert not {"json", "os", "__future__", "fs", "path", "app", "java.util", "com.shop"} & set(names)


def test_graph_has_import_links_and_libs(tmp_path: Path) -> None:
    for rel, text in FILES.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    idx = CodeIndex(tmp_path, tmp_path / "code.sqlite3", lambda: None)
    idx.refresh(embed=False)
    g = idx.graph()
    kinds = {(lk["source"], lk["target"]): lk["kind"] for lk in g["links"] if lk["kind"] == "import"}
    assert kinds[("web/src/main.ts", "web/src/App.vue")] == "import"
    vue = next(lib for lib in g["libs"] if lib["name"] == "vue")
    assert vue["ecosystem"] == "npm" and vue["count"] == 2 and vue["id"] == "npm:vue"
    idx.close()
