"""评测走真实 Runtime 和临时 git 项目，模型用剧本，检查命令用当前 Python。"""

import asyncio
import json
import shutil
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest

from app.llm import Usage
from app.runtime.mock import MockCoderModel
from app.runtime.shell import Shell, find_shell, run
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
async def test_hidden_tests_catch_a_fix_that_only_passes_visible_tests(tmp_path: Path) -> None:
    path = case_file(tmp_path, hidden="hidden")
    hidden = path.parent / "hidden"
    hidden.mkdir()
    (hidden / "test_hidden.py").write_text(
        "from cart import total\n\ndef test_other():\n    assert total([1], 2) == 3\n", encoding="utf-8")
    task = load_task(path)
    # 只对着看得到的测试凑答案：35
    cheat = Scripted([[call("read_file", path="cart.py")], [call("edit_file", path="cart.py", old_string="return sum(items)", new_string="return 35")], ["好了"]])
    result = await evaluate_task(task, lambda: cheat)
    assert not result.success and result.reason == "检查没通过" and "1 failed" in result.check_output
    assert result.changed == ["cart.py"]  # 隐藏测试是检查前才放进去的，不算她改的
    honest = Scripted([[call("read_file", path="cart.py")], [call("edit_file", path="cart.py", old_string="return sum(items)",
                             new_string="return sum(items) + shipping")], ["好了"]])
    assert (await evaluate_task(task, lambda: honest)).success


@pytest.mark.anyio
async def test_editing_protected_tests_is_not_a_fix(tmp_path: Path) -> None:
    task = load_task(case_file(tmp_path, protect=["test_*.py"]))
    model = Scripted([[call("read_file", path="test_cart.py")], [call("edit_file", path="test_cart.py", old_string="== 35", new_string="== 30")], ["测试过了"]])
    result = await evaluate_task(task, lambda: model)
    assert not result.success and result.reason == "改了不许改的文件"
    assert result.protected_changed == ["test_cart.py"]


@pytest.mark.anyio
async def test_read_only_task_fails_when_files_change(tmp_path: Path) -> None:
    task = load_task(case_file(tmp_path, check=None, answer_contains=["total"], expect_unchanged=True))
    sneaky = Scripted([[call("write_file", path="notes.md", content="草稿")], ["在 total 里"]])
    result = await evaluate_task(task, lambda: sneaky)
    assert not result.success and result.reason == "只读任务改了文件" and result.changed == ["notes.md"]
    assert (await evaluate_task(task, lambda: Scripted([["在 total 里"]]))).success


@pytest.mark.anyio
async def test_report_keeps_session_logs_and_groups_by_tag(tmp_path: Path) -> None:
    task = load_task(case_file(tmp_path, tags=["bugfix", "python"]))
    model = Scripted([[call("read_file", path="cart.py")], [call("edit_file", path="cart.py", old_string="return sum(items)",
                            new_string="return sum(items) + shipping")], ["好了"]])
    destination = tmp_path / "report"
    prices = Prices(Decimal("1"), Decimal("2"), Decimal("3"))
    [result] = await evaluate([task], lambda: model, destination, prices=prices)
    assert result.log == "sessions/cart-1.jsonl"
    log = [json.loads(line) for line in (destination / result.log).read_text(encoding="utf-8").splitlines()]
    assert any(e["type"] == "tool" and e["name"] == "edit_file" for e in log)
    report = (destination / "summary.md").read_text(encoding="utf-8")
    assert "## 按类别" in report and "| bugfix | 1 / 1 | 100.0% |" in report
    assert "## 每一次" in report and "[看](sessions/cart-1.jsonl)" in report and "cart.py" in report
    assert "每做成一个任务平均花费" in report


def test_hidden_and_protect_are_validated(tmp_path: Path) -> None:
    path = case_file(tmp_path)
    data = json.loads(path.read_text())
    for changes in ({"hidden": "repo"}, {"hidden": "missing"}, {"hidden": "..", "check": data["check"]},
                    {"protect": ["../x"]}, {"protect": "test_*.py"}, {"expect_unchanged": "yes"}):
        path.write_text(json.dumps({**data, **changes}), encoding="utf-8")
        with pytest.raises(ValueError):
            load_task(path)


def _check(task_dir: Path, task, project: Path, with_solution: bool) -> int:  # type: ignore[no-untyped-def]
    """把起点（可选：加上参考答案）和隐藏测试放进一个目录，跑检查命令"""
    shutil.copytree(task.fixture, project)
    if with_solution:
        shutil.copytree(task_dir / "solution", project, dirs_exist_ok=True)
    if task.hidden:
        shutil.copytree(task.hidden, project, dirs_exist_ok=True)
    shell = find_shell()
    return asyncio.run(run(shell, python_command(task.check_command, shell), project, 120)).exit_code  # type: ignore[return-value]


@pytest.mark.parametrize("task_dir", sorted(p.parent for p in TASKS.glob("*/task.json")), ids=lambda p: p.name)
def test_bundled_task_is_unsolved_at_start_and_solvable(task_dir: Path, tmp_path: Path) -> None:
    """每个任务：起点过不了检查（不是白送的），参考答案过得了（不是做不到的）；只读任务不带检查"""
    task = load_task(task_dir / "task.json")
    if task.check_command is None:
        assert task.answer_contains and task.expect_unchanged
        return
    assert (task_dir / "solution").is_dir(), "要有参考答案 solution/"
    assert _check(task_dir, task, tmp_path / "start", False) != task.expect_exit
    assert _check(task_dir, task, tmp_path / "solved", True) == task.expect_exit


def test_task_set_is_big_and_varied_enough() -> None:
    tasks = load_tasks(TASKS)
    tags = Counter(t for task in tasks for t in task.tags)
    assert len(tasks) >= 20
    for kind in ("bugfix", "feature", "refactor", "read-only", "traceback", "tests", "javascript"):
        assert tags[kind] >= 1, kind


@pytest.mark.anyio
async def test_mock_runs_every_task_and_writes_each_result(tmp_path: Path) -> None:
    tasks = load_tasks(TASKS, ["fix-cart-total", "locate-refund", "js-sort-prices"])
    destination = tmp_path / "report"
    results = await evaluate(tasks, lambda: MockCoderModel(delay=0), destination)
    rows = [json.loads(line) for line in (destination / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(results) == len(rows) == 3
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
