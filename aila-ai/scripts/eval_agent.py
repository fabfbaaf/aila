"""编程 Agent 评测：每次复制独立项目，用公开 Runtime 跑一轮，再检查代码或回答。

临时会话、规则和进程在每次评测结束时清理；报告逐条落盘，单个任务失败不影响后面的任务。
主 Agent 步数单独统计，token 则包含压缩和查资料助手，缓存命中率按总输入加权。
"""

import argparse
import asyncio
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.llm import CoderModel
from app.runtime.loop import AgentConfig
from app.runtime.mock import MockCoderModel
from app.runtime.runner import Runtime
from app.runtime.shell import Shell, clean_env, find_shell, run

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "eval" / "tasks"
TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")
# 只替换作为命令词的 python，避免改到脚本参数、文件名或字符串内容。
PYTHON_COMMAND = re.compile(r"(^|&&\s*|\|\|\s*|[;&|\n\r]\s*)\s*python(?=\s|$)")


@dataclass(frozen=True)
class TaskCase:
    id: str
    title: str
    fixture: Path
    prompt: str
    check_command: str | None = None
    expect_exit: int = 0
    answer_contains: tuple[str, ...] = ()
    max_steps: int = 30
    timeout_s: float = 600
    tags: tuple[str, ...] = ()


@dataclass
class EvalResult:
    id: str
    title: str
    iteration: int
    success: bool = False
    reason: str = "error"
    steps: int = 0
    tools: dict[str, int] = field(default_factory=dict)
    seconds: float = 0
    tokens: dict[str, int] = field(default_factory=lambda: dict(prompt=0, cache_hit=0, cache_miss=0, completion=0))
    cache_hit_rate: float = 0
    estimated: bool = False
    turn_reason: str = ""
    confirmations_refused: int = 0
    check_exit_code: int | None = None
    check_output: str = ""
    answer: str = ""
    detail: str = ""


@dataclass(frozen=True)
class Prices:
    hit: Decimal
    miss: Decimal
    output: Decimal

    def cost(self, tokens: Mapping[str, int]) -> Decimal:
        return (tokens["cache_hit"] * self.hit + tokens["cache_miss"] * self.miss
                + tokens["completion"] * self.output) / Decimal(1_000_000)


def load_task(path: Path) -> TaskCase:
    """任务路径只在它自己的目录里，格式写错时在启动模型之前报清楚。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} 要是一个 JSON 对象。")
    for key in ("id", "title", "fixture", "prompt"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f"{path} 的 {key} 不能为空。")
    if not TASK_ID.fullmatch(data["id"]):
        raise ValueError(f"{path} 的 id 只能用字母、数字、下划线和连字符。")
    root = path.parent.resolve()
    fixture = (root / data["fixture"]).resolve()
    if not fixture.is_relative_to(root) or fixture == root or not fixture.is_dir():
        raise ValueError(f"{path} 的 fixture 要指向任务目录里的子目录。")
    check = data.get("check")
    if check is not None and (not isinstance(check, dict) or not isinstance(check.get("command"), str)
                              or not check["command"].strip()):
        raise ValueError(f"{path} 的 check.command 要是一条命令。")
    expect = check.get("expect_exit", 0) if check else 0
    if type(expect) is not int:
        raise ValueError(f"{path} 的 expect_exit 要是整数。")
    words = data.get("answer_contains", [])
    tags = data.get("tags", [])
    for name, values in (("answer_contains", words), ("tags", tags)):
        if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
            raise ValueError(f"{path} 的 {name} 要是非空字符串的列表。")
    if not check and not words:
        raise ValueError(f"{path} 至少要有 check 或 answer_contains。")
    steps, timeout = data.get("max_steps", 30), data.get("timeout_s", 600)
    if type(steps) is not int or steps < 1:
        raise ValueError(f"{path} 的 max_steps 要是正整数。")
    if type(timeout) not in (float, int) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError(f"{path} 的 timeout_s 要是大于零的秒数。")
    return TaskCase(data["id"], data["title"], fixture, data["prompt"],
                    check["command"] if check else None, expect, tuple(words), steps, float(timeout), tuple(tags))


def load_tasks(root: Path, only: Sequence[str] = ()) -> list[TaskCase]:
    tasks = [load_task(path) for path in sorted(root.glob("*/task.json"))]
    counts = Counter(task.id for task in tasks)
    if any(n > 1 for n in counts.values()):
        raise ValueError("任务 id 重复了，每个任务要用不同的 id。")
    missing = set(only) - counts.keys()
    if missing:
        raise ValueError("没有这些任务：" + "、".join(sorted(missing)))
    selected = [task for task in tasks if not only or task.id in only]
    if not selected:
        raise ValueError(f"{root} 里没有 task.json 任务。")
    return selected


def python_command(command: str, shell: Shell) -> str:
    """检查命令用同一份虚拟环境；PowerShell 调带引号的路径需要 &。"""
    executable = Path(sys.executable).as_posix()
    # 先遮住引号和转义里的文字，再定位命令词；保留位置，所以不会重写参数或吞掉原来的空白。
    masked: list[str] = []
    quote, escaped = "", False
    escape = "`" if shell.kind == "powershell" else "\\"
    for char in command:
        if escaped:
            masked.append("x")
            escaped = False
        elif char == escape and quote != "'":
            masked.append("x")
            escaped = True
        elif quote:
            masked.append("x")
            if char == quote:
                quote = ""
        elif char in ("'", '"'):
            masked.append("x")
            quote = char
        else:
            masked.append(char)
    parts, end = [], 0
    for match in PYTHON_COMMAND.finditer("".join(masked)):
        start = match.end() - len("python")
        prefix = match.group(1).strip()
        operator = "& " if shell.kind == "powershell" and prefix != "&" else ""
        parts.extend([command[end:start], operator + f'"{executable}"'])
        end = match.end()
    return "".join([*parts, command[end:]])


def init_repo(project: Path) -> None:
    for args in (("init", "-q"), ("add", "."), ("commit", "-q", "-m", "评测起点")):
        subprocess.run(["git", "-c", f"safe.directory={project.as_posix()}", "-c", "user.name=艾拉评测",
                        "-c", "user.email=eval@localhost", *args], cwd=project, env=clean_env(),
                       check=True, capture_output=True, timeout=30)


async def evaluate_task(task: TaskCase, model_factory: Callable[[], CoderModel], iteration: int = 1,
                        shell: Shell | None = None) -> EvalResult:
    """每次都新建 Runtime；超时会同时打断当轮并收回它开的后台进程。"""
    result = EvalResult(task.id, task.title, iteration)
    started = time.perf_counter()
    shell = shell or find_shell()
    with tempfile.TemporaryDirectory(prefix=f"aila-eval-{task.id}-") as temp:
        root = Path(temp)
        rt: Runtime | None = None
        session = None
        try:
            project = root / "repo"
            shutil.copytree(task.fixture, project, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
            init_repo(project)
            rt = Runtime(root / "data", model_factory, shell=shell, config=AgentConfig(max_steps=task.max_steps))

            def count_step(**_: Any) -> None:
                result.steps += 1

            rt.hooks.on("pre_step", count_step)
            session = rt.create(str(project), "auto")
            async with asyncio.timeout(max(0.001, task.timeout_s - (time.perf_counter() - started))):
                async for event in rt.run(session.id, task.prompt):
                    if event["type"] == "confirm":
                        result.confirmations_refused += 1
                        rt.confirms.answer(event["id"], False)
                result.turn_reason = next((e["reason"] for e in reversed(session.events) if e["type"] == "turn_end"), "error")
                result.answer = next((e.get("text", "") for e in reversed(session.events)
                                      if e["type"] == "assistant" and not e.get("tool_calls")), "")
                if task.check_command:
                    remaining = task.timeout_s - (time.perf_counter() - started)
                    checked = await run(shell, python_command(task.check_command, shell), project, remaining)
                    result.check_exit_code = checked.exit_code
                    result.check_output = "\n".join(checked.output.splitlines()[-30:])
                    if checked.exit_code is None:
                        raise TimeoutError
                if result.turn_reason != "done":
                    result.reason = result.turn_reason
                elif task.check_command and result.check_exit_code != task.expect_exit:
                    result.reason = "检查没通过"
                elif any(word not in result.answer for word in task.answer_contains):
                    result.reason = "回答缺少要求的词"
                else:
                    result.success, result.reason = True, ""
        except TimeoutError:
            result.reason = "timeout"
            if rt and session:
                rt.interrupt(session.id)
        except Exception as err:
            result.reason = "error"
            result.detail = f"{type(err).__name__}：{err}"[:2000]
        finally:
            if rt:
                await rt.shutdown()
            if session:
                usage = session.usage
                result.tokens = {key: int(getattr(usage, key)) for key in result.tokens}
                result.estimated = usage.estimated
                result.cache_hit_rate = usage.cache_hit / usage.prompt if usage.prompt else 0
                result.tools = dict(Counter(e["name"] for e in session.events if e["type"] == "tool"))
                result.turn_reason = result.turn_reason or next(
                    (e["reason"] for e in reversed(session.events) if e["type"] == "turn_end"), "error")
            result.seconds = round(time.perf_counter() - started, 3)
    return result


def read_prices(env: Mapping[str, str] | None = None) -> Prices | None:
    env = os.environ if env is None else env
    keys = ("AILA_PRICE_HIT", "AILA_PRICE_MISS", "AILA_PRICE_OUTPUT")
    if not all(env.get(key, "").strip() for key in keys):
        return None
    try:
        values = [Decimal(env[key]) for key in keys]
    except InvalidOperation as err:
        raise ValueError("价格要填每百万 token 的元数。") from err
    if any(not value.is_finite() or value < 0 for value in values):
        raise ValueError("价格要是有限的非负数。")
    return Prices(*values)


def totals(results: Sequence[EvalResult]) -> dict[str, Any]:
    tokens = {key: sum(r.tokens[key] for r in results) for key in ("prompt", "cache_hit", "cache_miss", "completion")}
    count = len(results)
    return {
        "count": count, "successes": sum(r.success for r in results),
        "success_rate": sum(r.success for r in results) / count if count else 0,
        "average_steps": sum(r.steps for r in results) / count if count else 0,
        "average_seconds": sum(r.seconds for r in results) / count if count else 0,
        "tokens": tokens, "cache_hit_rate": tokens["cache_hit"] / tokens["prompt"] if tokens["prompt"] else 0,
    }


def summary(results: Sequence[EvalResult], prices: Prices | None = None) -> str:
    lines = ["# 编程 Agent 评测", "", "步数只计主 Agent；token 含压缩和助手。缓存命中率 = 命中输入 / 总输入。", "",
             "| 任务 | 做成 / 次数 | 成功率 | 平均步数 | 平均秒数 | 输入 | 命中 | 未命中 | 输出 | 命中率 |"
             + (" 费用（元） |" if prices else ""),
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|" + ("---:|" if prices else "")]
    groups = [(id, [r for r in results if r.id == id]) for id in dict.fromkeys(r.id for r in results)]
    for label, group in [*groups, ("合计", list(results))]:
        stats = totals(group)
        token = stats["tokens"]
        safe_label = label.replace("|", "\\|").replace("\n", " ")
        line = (f"| {safe_label} | {stats['successes']} / {stats['count']} | {stats['success_rate']:.1%} | "
                f"{stats['average_steps']:.2f} | {stats['average_seconds']:.3f} | {token['prompt']} | "
                f"{token['cache_hit']} | {token['cache_miss']} | {token['completion']} | {stats['cache_hit_rate']:.1%} |")
        lines.append(line + (f" {prices.cost(token):.6f} |" if prices else ""))
    lines.extend(["", "## 失败原因", ""])
    reasons = Counter(r.reason for r in results if not r.success)
    lines.extend([f"- {reason}：{count}" for reason, count in sorted(reasons.items())] or ["没有失败的任务。"])
    if any(r.estimated for r in results):
        lines.extend(["", "含假模型估算的 token，不是真实账单。"])
    return "\n".join(lines) + "\n"


async def evaluate(tasks: Sequence[TaskCase], model_factory: Callable[[], CoderModel], output: Path,
                   repeat: int = 1, prices: Prices | None = None) -> list[EvalResult]:
    if repeat < 1:
        raise ValueError("repeat 要大于零。")
    output.mkdir(parents=True, exist_ok=False)
    results: list[EvalResult] = []
    with (output / "results.jsonl").open("w", encoding="utf-8") as stream:
        for task in tasks:
            for iteration in range(1, repeat + 1):
                result = await evaluate_task(task, model_factory, iteration)
                row = asdict(result)
                if prices:
                    row["cost_yuan"] = float(prices.cost(result.tokens))
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                stream.flush()
                results.append(result)
                (output / "summary.md").write_text(summary(results, prices), encoding="utf-8")
                print(f"{'✓' if result.success else '✗'} {task.id} [{iteration}/{repeat}] · {result.steps} 步 · "
                      f"{result.seconds:.2f} 秒 · 命中 {result.cache_hit_rate:.1%}"
                      + (f" · {result.reason}" if result.reason else ""), flush=True)
    return results


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="在独立临时项目里评测编程 Agent，自动拒绝所有确认。")
    parser.add_argument("--model", choices=("settings", "mock"), default="settings", help="默认使用设置里的编程模型")
    parser.add_argument("--only", nargs="+", default=[], metavar="任务ID")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--tasks-dir", type=Path, default=TASKS)
    parser.add_argument("--output-dir", type=Path, help="新的报告目录，默认 eval/results/时间")
    args = parser.parse_args(argv)
    try:
        if args.repeat < 1:
            raise ValueError("repeat 要大于零。")
        tasks, prices = load_tasks(args.tasks_dir, args.only), read_prices()
        output = args.output_dir or ROOT / "eval" / "results" / datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
        if args.model == "mock":
            factory: Callable[[], CoderModel] = lambda: MockCoderModel(delay=0)
        else:
            from app.deps import get_coder_model
            factory = get_coder_model
        asyncio.run(evaluate(tasks, factory, output, args.repeat, prices))
    except (ValueError, OSError, json.JSONDecodeError) as err:
        parser.error(str(err))
    print(f"报告在 {output}")
    # 评测没做成属于结果，不是脚手架故障；mock 模型也能完整跑通并以 0 退出。
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
