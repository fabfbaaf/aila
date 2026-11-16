"""评测走真实 Runtime 和临时 git 项目，模型用剧本，检查命令用当前 Python。"""

import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from app.llm import Usage
from app.runtime.mock import MockCoderModel
from app.runtime.shell import Shell
from scripts.eval_agent import (
    TASKS, EvalResult, Prices, evaluate, evaluate_task, load_task, load_tasks, main,
    python_command, read_prices, summary, totals,
)
from tests.test_runtime import Scripted, call


def case_file(tmp_path: Path, **overrides: object) -> Path:
    root = tmp_path / "case"
    fixture = root / "repo"
    fixture.mkdir(parents=True)
    (fixture / "cart.py").write_text("def total(items, shipping):\n    return sum(items)\n", encoding="utf-8")
    (fixture / "test_cart.py").write_text(
        "from cart import total\n\ndef test_total():\n    assert total([10, 20], 5) == 35\n", encoding="utf-8")
    data = {"id": "cart", "title": "修好运费", "fixture": "repo", "prompt": "修好运费并验证",
            "check": {"command": "python -m pytest -q", "expect_exit": 0}, **overrides}
    path = root / "task.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.mark.anyio
async def test_scripted_fix_is_checked_and_metrics_are_exact(tmp_path: Path) -> None:
    task = load_task(case_file(tmp_path))
    model = Scripted([
        [call("read_file", path="cart.py"), Usage(100, 10, 20, 80)],
        [call("edit_file", path="cart.py", old_string="return sum(items)", new_string="return sum(items) + shipping"),
         Usage(200, 20, 150, 50)],
        ["已经修好。", Usage(300, 30, 250, 50)],
    ])
    result = await evaluate_task(task, lambda: model)
    assert result.success and result.reason == "" and result.turn_reason == "done"
    assert result.steps == 3 and result.tools == {"read_file": 1, "edit_file": 1}
    assert result.tokens == {"prompt": 600, "completion": 60, "cache_hit": 420, "cache_miss": 180}
    assert result.cache_hit_rate == pytest.approx(0.7)
    assert result.check_exit_code == 0 and "1 passed" in result.check_output
    assert result.seconds > 0 and result.confirmations_refused == 0
    assert " + shipping" not in (task.fixture / "cart.py").read_text()
    temporary_project = Path(model.requests[0][0][1]["content"].split("项目目录：", 1)[1].splitlines()[0])
    assert not temporary_project.exists()


@pytest.mark.anyio
async def test_wrong_fix_fails_check(tmp_path: Path) -> None:
    task = load_task(case_file(tmp_path))
    model = Scripted([[call("read_file", path="cart.py")],
                      [call("edit_file", path="cart.py", old_string="sum(items)", new_string="sum(items) + 1")], ["改好了"]])
    result = await evaluate_task(task, lambda: model)
    assert not result.success and result.reason == "检查没通过"
    assert result.check_exit_code == 1 and "failed" in result.check_output


@pytest.mark.anyio
async def test_max_steps_is_failure_even_when_answer_matches(tmp_path: Path) -> None:
    task = load_task(case_file(tmp_path, check=None, answer_contains=["文件"], max_steps=2))
    model = Scripted([["文件", call("list_files")]])
    result = await evaluate_task(task, lambda: model)
    assert not result.success and result.reason == "max_steps" and result.turn_reason == "max_steps"
    assert result.steps == 2 and result.tools == {"list_files": 2}


@pytest.mark.anyio
@pytest.mark.parametrize("answer, success", [("billing.py 里的 process_refund", True), ("在账单里", False)])
async def test_read_only_answer_requires_all_words(tmp_path: Path, answer: str, success: bool) -> None:
    task = load_task(case_file(tmp_path, check=None, answer_contains=["billing.py", "process_refund"]))
    result = await evaluate_task(task, lambda: Scripted([[answer]]))
    assert result.success == success
    assert result.reason == ("" if success else "回答缺少要求的词")
    assert result.check_exit_code is None


@pytest.mark.anyio
async def test_dangerous_command_is_refused_without_execution(tmp_path: Path) -> None:
    task = load_task(case_file(tmp_path, check=None, answer_contains=["完成"]))
    model = Scripted([[call("run_command", command="rm -rf sentinel")], ["完成"]])
    result = await evaluate_task(task, lambda: model)
    assert not result.success and result.reason == "rejected"
    assert result.confirmations_refused == 1 and result.turn_reason == "rejected"
    assert result.tools == {"run_command": 1}


@pytest.mark.anyio
async def test_timeout_interrupts_the_independent_runtime_task(tmp_path: Path) -> None:
    task = load_task(case_file(tmp_path, check=None, answer_contains=["完成"], timeout_s=0.5))
    model = Scripted([["完成"]], delay=5)
    result = await asyncio.wait_for(evaluate_task(task, lambda: model), 5)
    assert not result.success and result.reason == "timeout"
    assert result.turn_reason == "interrupted"


def test_summary_aggregates_tokens_weights_rates_and_optional_cost() -> None:
    first = EvalResult("one", "一", 1, success=True, reason="", steps=2, seconds=2,
                       tokens=dict(prompt=100, cache_hit=90, cache_miss=10, completion=5))
    second = EvalResult("one", "一", 2, reason="检查没通过", steps=4, seconds=4,
                        tokens=dict(prompt=300, cache_hit=30, cache_miss=270, completion=15))
    stats = totals([first, second])
    assert stats["success_rate"] == 0.5 and stats["average_steps"] == 3
    assert stats["average_seconds"] == 3 and stats["cache_hit_rate"] == 0.3
    assert stats["tokens"] == dict(prompt=400, cache_hit=120, cache_miss=280, completion=20)
    prices = Prices(Decimal("1"), Decimal("2"), Decimal("3"))
    assert prices.cost(stats["tokens"]) == Decimal("0.00074")
    report = summary([first, second], prices)
    assert "| 合计 | 1 / 2 | 50.0% | 3.00 | 3.000 | 400 | 120 | 280 | 20 | 30.0% | 0.000740 |" in report
    assert "检查没通过：1" in report
    assert "费用" not in summary([first, second])
    assert totals([])["success_rate"] == 0


def test_prices_require_all_three_and_reject_invalid_values() -> None:
    assert read_prices({}) is None
    assert read_prices({"AILA_PRICE_HIT": "1"}) is None
    values = {"AILA_PRICE_HIT": "1", "AILA_PRICE_MISS": "2", "AILA_PRICE_OUTPUT": "3"}
    assert read_prices(values) == Prices(Decimal(1), Decimal(2), Decimal(3))
    for bad in ("NaN", "Infinity", "-1", "乱写"):
        with pytest.raises(ValueError):
            read_prices({**values, "AILA_PRICE_HIT": bad})


def test_tasks_validate_location_grader_and_selection(tmp_path: Path) -> None:
    path = case_file(tmp_path)
    assert load_tasks(path.parent.parent, ["cart"])[0].id == "cart"
    with pytest.raises(ValueError, match="没有这些任务"):
        load_tasks(path.parent.parent, ["missing"])
    data = json.loads(path.read_text())
    for changes in ({"fixture": ".."}, {"check": None}, {"max_steps": 0}, {"timeout_s": -1}, {"id": "../bad"}):
        path.write_text(json.dumps({**data, **changes}), encoding="utf-8")
        with pytest.raises(ValueError):
            load_task(path)


def test_python_replacement_uses_current_interpreter_without_touching_arguments() -> None:
    executable = Path(sys.executable).as_posix()
    assert python_command("python -m pytest -q", Shell("bash", "bash")) == f'"{executable}" -m pytest -q'
    assert python_command("python -m pytest", Shell("powershell", "pwsh")) == f'& "{executable}" -m pytest'
    assert python_command('echo "python is a language"', Shell("bash", "bash")) == 'echo "python is a language"'
    assert python_command("python script.py; python other.py", Shell("bash", "bash")).count(executable) == 2
    quoted = 'echo "a; python is text"; python -m pytest'
    assert python_command(quoted, Shell("bash", "bash")) == f'echo "a; python is text"; "{executable}" -m pytest'
    assert python_command("& python -m pytest", Shell("powershell", "pwsh")) == f'& "{executable}" -m pytest'


@pytest.mark.anyio
async def test_mock_completes_all_five_tasks_and_writes_each_result(tmp_path: Path) -> None:
    tasks = load_tasks(TASKS)
    assert len(tasks) == 5
    destination = tmp_path / "report"
    results = await evaluate(tasks, lambda: MockCoderModel(delay=0), destination)
    rows = [json.loads(line) for line in (destination / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(results) == len(rows) == 5
    assert {row["id"] for row in rows} == {task.id for task in tasks}
    assert all(row["turn_reason"] == "done" and row["reason"] != "error" for row in rows)
    assert all("cost_yuan" not in row for row in rows)
    assert "合计" in (destination / "summary.md").read_text(encoding="utf-8")


def test_cli_selects_and_repeats_without_loading_real_model(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    destination = tmp_path / "selected"
    assert main(["--model", "mock", "--only", "locate-refund", "--repeat", "2", "--output-dir", str(destination)]) == 0
    rows = [json.loads(line) for line in (destination / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [row["iteration"] for row in rows] == [1, 2]
    assert all(row["id"] == "locate-refund" for row in rows)
    assert "[2/2]" in capsys.readouterr().out
