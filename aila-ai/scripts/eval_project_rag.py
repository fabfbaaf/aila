"""评测真实仓库的项目 RAG，用同一题集比较召回、重排和多跳。默认离线，只测检索。"""

import argparse
import asyncio
import hashlib
import json
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.code import CodeIndex  # noqa: E402
from app.code.evidence import DecisionSource, RetrievalTrace, Source, collect  # noqa: E402
from app.knowledge import KnowledgeBase  # noqa: E402
from app.knowledge.rerank import local_reranker  # noqa: E402
from app.llm import CoderModel, Usage  # noqa: E402
from app.memory import FastEmbedder  # noqa: E402
from app.runtime.evidencetool import decision_sources  # noqa: E402
from app.runtime.memory import ProjectMemory  # noqa: E402
from app.runtime.session import SessionStore  # noqa: E402


def supports(source: Source, expected: dict[str, Any]) -> bool:
    return (
        expected["quote"] in source.text
        and ("file" not in expected or getattr(source, "file", None) == expected["file"])
        and ("document" not in expected or getattr(source, "document", None) == expected["document"])
        and ("decision" not in expected or isinstance(source, DecisionSource))
    )


async def check_answer(model: CoderModel, case: dict[str, Any], sources: list[Source]) -> dict[str, Any]:
    messages = [
        {"role": "system", "content": (
            "在不调用工具的前提下，用中文回答问题。根据上下文回答，缺证据时说明。"
            "旧版本项目决定有 until，注意先后。输出 JSON 对象，answer 为答案字符串，citations 为数组，"
            "每项有 id（来源 ID）和 quote（该来源的原文一句）。没有给来源时 citations 写空数组。"
        )},
        {"role": "user", "content": json.dumps({
            "question": case["question"], "sources": [asdict(s) for s in sources],
        }, ensure_ascii=False)},
    ]
    start = time.perf_counter()
    chunks = []
    usage = None
    raw_sources = {s.id: s for s in sources}
    try:
        async with asyncio.timeout(90):
            async for part in model.step(messages, None, max_tokens=1500):
                if isinstance(part, str):
                    chunks.append(part)
                elif isinstance(part, Usage):
                    usage = part
        raw = "".join(chunks).strip()
        if raw.startswith("\x60\x60\x60"):
            raw = raw.split("\n", 1)[-1].rsplit("\x60\x60\x60", 1)[0]
        data = json.loads(raw)
        text = data["answer"]
        if not isinstance(text, str) or not isinstance(data.get("citations"), list):
            raise ValueError
    except Exception as err:
        return {"error": type(err).__name__, "model_ms": round(1000 * (time.perf_counter() - start)), "accuracy": False}

    good, citations = 0, data["citations"]
    for cite in citations:
        if not isinstance(cite, dict) or not isinstance(cite.get("id"), str) or not isinstance(cite.get("quote"), str):
            continue
        source = raw_sources.get(cite["id"])
        if not source or not cite["quote"].strip() or cite["quote"] not in source.text:
            continue
        if any(supports(source, gold) and gold["quote"] in cite["quote"] for gold in case["required"]):
            good += 1
    normalized = "".join(text.lower().split())
    return {
        "accuracy": all(any("".join(term.lower().split()) in normalized for term in group) for group in case["answer_terms"]),
        "citation_precision": good / len(citations) if citations else None,
        "citations": len(citations),
        "input_tokens": usage.prompt if usage else None,
        "model_ms": round(1000 * (time.perf_counter() - start)),
        "answer": text,
    }


async def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    case_bytes = args.cases.read_bytes()
    cases = json.loads(case_bytes)
    model = None
    if args.with_model:
        from app.deps import get_coder_model, settings
        if settings.mock:
            raise SystemExit("需要配置编程模型才能运行回答评测。")
        model = get_coder_model()
    score = local_reranker(str(args.rerank_model_dir)) if args.rerank_model_dir else None
    if args.rerank_model_dir and score is None:
        raise SystemExit("本地重排模型加载失败。检查 --rerank-model-dir。")
    encoder = FastEmbedder(args.models_dir) if args.models_dir else None
    modes = [
        ("无检索", None, 0, False),
        ("混合检索", None, 0, True),
        *([("混合检索 + 重排", score, 0, True)] if score else []),
        ("混合检索 + 多跳", None, 3, True),
        *([("混合检索 + 重排 + 多跳", score, 3, True)] if score else []),
    ]
    results = []
    with tempfile.TemporaryDirectory(prefix="aila-rag-eval-") as data_dir:
        index = CodeIndex(args.root, ":memory:", lambda: encoder)
        memory = ProjectMemory(Path(data_dir) / "project_memory.json")
        sessions = SessionStore(Path(data_dir) / "sessions")
        try:
            index.refresh(embed=encoder is not None)
            for case in cases:
                kb = KnowledgeBase(":memory:", lambda: encoder)
                try:
                    linked = [kb.add(d["name"], d["text"].encode()).id for d in case.get("documents", [])]
                    kb.set_project_documents(case["id"], linked)
                    previous = None
                    for text in case.get("decisions", []):
                        previous, _ = memory.add(args.root, text, replaces=previous.id if previous else None)
                    for mode, scorer, depth, enabled in modes:
                        trace = RetrievalTrace()
                        started = time.perf_counter()
                        sources = collect(index, kb, case["id"], case["question"], scorer=scorer, depth=depth,
                                          trace=trace) if enabled else []
                        if enabled and case.get("decisions"):
                            sources.extend(decision_sources(memory, sessions, args.root, case["question"],
                                                            history=case.get("history", False)))
                        retrieve_ms = round(1000 * (time.perf_counter() - started))
                        gold = case["required"]
                        found = [target for target in gold if any(supports(s, target) for s in sources)]
                        row = {
                            "case": case["id"], "mode": mode, "retrieve_ms": retrieve_ms,
                            "evidence_required": len(gold), "evidence_found": len(found),
                            "missing": [target for target in gold if target not in found],
                            "sources": len(sources), "context_chars": sum(len(s.text) for s in sources),
                            "reranked": trace.reranked,
                        }
                        if model is not None:
                            row.update(await check_answer(model, case, sources))
                        results.append(row)
                    if previous is not None:
                        memory.remove(args.root, previous.id)
                finally:
                    kb.close()
        finally:
            index.close()
    revisions = subprocess.run(
        ["git", "-C", str(args.root), "rev-parse", "HEAD"], capture_output=True, text=True,
        check=False, timeout=10,
    )
    changed = subprocess.run(
        ["git", "-C", str(args.root), "status", "--porcelain", "--untracked-files=no"],
        capture_output=True, text=True, check=False, timeout=10,
    )
    return {
        "revision": revisions.stdout.strip() or None, "working_tree_dirty": bool(changed.stdout.strip()),
        "case_sha256": hashlib.sha256(case_bytes).hexdigest(), "model": model.name if model else None,
        "vectors": encoder is not None, "local_reranker": bool(score), "cases": len(cases), "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT.parent)
    parser.add_argument("--cases", type=Path, default=Path(__file__).with_name("project_rag_cases.json"))
    parser.add_argument("--report", type=Path, help="把分题指标和缺失依据写入 JSON")
    parser.add_argument("--models-dir", type=Path, help="使用指定目录里的向量模型缓存")
    parser.add_argument("--rerank-model-dir", type=Path, help="比较纯混合检索和本地重排")
    parser.add_argument("--with-model", action="store_true", help="使用配置的编程模型，测回答、引用和输入 token")
    args = parser.parse_args()
    report = asyncio.run(evaluate(args))
    rows = report["results"]
    for mode in dict.fromkeys(row["mode"] for row in rows):
        mode_rows = [row for row in rows if row["mode"] == mode]
        have = sum(row["evidence_found"] for row in mode_rows)
        need = sum(row["evidence_required"] for row in mode_rows)
        ms = statistics.median(row["retrieve_ms"] for row in mode_rows)
        print(f"{mode}：必要证据 {have}/{need}，检索中位 {ms:g} ms")
        for row in mode_rows:
            coverage = f"{row['evidence_found']}/{row['evidence_required']}" if row["evidence_required"] else "无标注证据"
            print(f"  {row['case']}  {coverage}  {row['sources']} 段  {row['context_chars']} 字符")
        if args.with_model:
            correct = sum(bool(row.get("accuracy")) for row in mode_rows)
            print(f"  答案按关键词校验 {correct}/{len(mode_rows)}，引用精度和 token 见 JSON 结果。")
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
