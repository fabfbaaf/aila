"""代码检索评测：对一个项目建索引，用 code_eval.json 里的问题分别测每一路检索和合起来的效果。

用法（在 aila-ai 目录下）：
    .venv\\Scripts\\python scripts\\eval_code_rag.py            # 默认测艾拉自己的仓库
    .venv\\Scripts\\python scripts\\eval_code_rag.py --root D:\\other --cases my_eval.json

指标：Top-1 / Top-5 命中率（第一条 / 前五条里有没有对的），MRR（对的那条排第几的倒数取平均）。
堆栈定位单独算：报错文字 → 定位到的函数对不对。
"""

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.code import CodeIndex  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.memory import FastEmbedder  # noqa: E402

ALL = ("exact", "keyword", "vector")
# 方式名 → (用哪几路, 往文件里细找放在前面 / 后面 / 不找)
MODES = {
    "只用名字": (("exact",), None),
    "只用关键词": (("keyword",), None),
    "只用向量": (("vector",), None),
    "关键词 + 向量": (("keyword", "vector"), None),
    "三路合并": (ALL, None),
    "三路 + 细找(后)": (ALL, "after"),
    "三路 + 细找(前)": (ALL, "before"),
}
FINAL = "三路 + 细找(前)"


def hit(qname: str, name: str, expect: list[str]) -> bool:
    return any(qname == e or qname.endswith("." + e) or name == e for e in expect)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(ROOT.parent))
    parser.add_argument("--cases", default=str(Path(__file__).with_name("code_eval.json")))
    parser.add_argument("--db", default=None, help="索引文件（默认临时文件，每次重建）")
    args = parser.parse_args()

    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    embedder = FastEmbedder(load_settings().models_dir)
    db = Path(args.db) if args.db else Path(tempfile.mkdtemp()) / "eval.sqlite3"
    index = CodeIndex(Path(args.root), db, lambda: embedder)
    stats = index.refresh()
    print(f"索引：{stats.files} 个文件、{stats.chunks} 块，{stats.seconds} 秒\n")

    print(f"{'方式':<14}{'Top-1':>8}{'Top-5':>8}{'MRR':>8}{'平均耗时':>10}")
    misses: dict[str, list[str]] = {}
    for label, (modes, drill) in MODES.items():
        top1 = top5 = 0
        rr = 0.0
        t0 = time.perf_counter()
        for case in cases["queries"]:
            hits = index.search(case["q"], k=5, modes=modes, drill=drill)
            ranks = [i for i, h in enumerate(hits) if hit(h.qname, h.name, case["expect"])]
            if ranks:
                top1 += ranks[0] == 0
                top5 += 1
                rr += 1 / (ranks[0] + 1)
            else:
                misses.setdefault(label, []).append(f"{case['q']} → {[h.qname for h in hits[:3]]}")
        n = len(cases["queries"])
        ms = (time.perf_counter() - t0) * 1000 / n
        print(f"{label:<14}{top1 / n:>8.0%}{top5 / n:>8.0%}{rr / n:>8.2f}{ms:>8.0f}ms")

    print(f"\n{FINAL}没找到的：")
    for line in misses.get(FINAL, []) or ["（都找到了）"]:
        print("  ", line)

    ok = 0
    for case in cases["traces"]:
        located = [loc for loc in index.locate(case["text"]) if loc.chunk]
        got = located[0].chunk.qname if located else None
        ok += got == case["expect"]
        if got != case["expect"]:
            print(f"   堆栈定位错了：期望 {case['expect']}，得到 {got}")
    print(f"\n堆栈定位：{ok}/{len(cases['traces'])} 定位到正确的函数")


if __name__ == "__main__":
    main()
