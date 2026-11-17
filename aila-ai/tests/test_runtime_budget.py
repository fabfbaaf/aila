"""花费与上限按真实事件链计算；假价格和剧本模型保证验收不访问模型服务。"""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from app.cli.app import Terminal, headless, open_session, parse_args
from app.cli.backend import EmbeddedBackend, HttpBackend
from app.cli.commands import usage_lines
from app.cli.render import Renderer
from app.deps import get_runtime
from app.llm import StepItem, Usage
from app.main import app
from app.runtime.loop import AgentConfig
from app.runtime.pricing import PriceTable, cost, validate_budget
from app.runtime.runner import Runtime, RuntimeError_
from app.runtime.session import SessionStore

from .test_cli import recording
from .test_runtime import SHELL, Scripted, call

MODEL = "test-budget-model"
PRICES = {MODEL: {"hit": 1, "miss": 4, "output": 2}, "test-fast-model": {"hit": 2, "miss": 6, "output": 3}}
STEP_USAGE = Usage(prompt=1000, completion=0, cache_hit=0, cache_miss=1000)


def price_file(tmp_path: Path, prices: dict[str, Any] | None = None) -> Path:
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    path = data / "prices.json"
    path.write_text(json.dumps(PRICES if prices is None else prices), encoding="utf-8")
    return path


def scripted(*steps: list[StepItem], name: str = MODEL) -> Scripted:
    model = Scripted(list(steps))
    model.name = name
    return model


def runtime(tmp_path: Path, model: Scripted, config: AgentConfig | None = None) -> Runtime:
    price_file(tmp_path)
    return Runtime(tmp_path / "data", lambda: model, shell=SHELL, config=config)


async def run_turn(rt: Runtime, sid: str, text: str = "接着做") -> list[dict[str, Any]]:
    events = [event async for event in rt.run(sid, text)]
    assert events[0]["type"] == "user" and events[-1]["type"] == "turn_end"
    return events


def usage_event(model: str = MODEL, **values: int) -> dict[str, Any]:
    return {"type": "usage", "model": model, "prompt": 1000, "cache_hit": 250,
            "cache_miss": 750, "completion": 100, **values}


def test_price_components_and_user_model_override(tmp_path: Path) -> None:
    table = PriceTable(price_file(tmp_path))
    event = usage_event()
    assert table.cost(event) == pytest.approx(0.00345)
    assert cost(event, table) == pytest.approx(0.00345)
    assert cost(usage_event("unpriced-model"), table) is None
    assert cost(usage_event("mock-coder"), table) is None
    overridden = PriceTable(price_file(tmp_path, {MODEL: {"hit": "2.5", "miss": "3", "output": "5"}}))
    assert overridden.cost(event) == pytest.approx(0.003375)


def test_default_deepseek_prices_are_peak_and_retired_names_stay_unknown(tmp_path: Path) -> None:
    table = PriceTable()  # 没有价格表文件：只用内置价格
    assert table.cost(usage_event("deepseek-flash")) == pytest.approx(0.00231)
    assert table.cost(usage_event("deepseek-v4-flash")) == pytest.approx(0.00231)
    assert table.cost(usage_event("deepseek-v4-pro")) == pytest.approx(0.009525)
    assert table.cost(usage_event("deepseek-chat")) is None
    assert table.cost(usage_event("deepseek-reasoner")) is None
    off_peak = PriceTable(price_file(tmp_path, {"deepseek-flash": {"hit": 0.02, "miss": 1, "output": 4}}))
    assert off_peak.cost(usage_event("deepseek-flash")) == pytest.approx(0.001155)


@pytest.mark.parametrize("prices", [
    {},
    {MODEL: {"hit": 1, "miss": 4}},
    {MODEL: {"hit": -1, "miss": 4, "output": 2}},
    {MODEL: {"hit": True, "miss": 4, "output": 2}},
    {MODEL: {"hit": "NaN", "miss": 4, "output": 2}},
    {MODEL: {"hit": 1, "miss": "Infinity", "output": 2}},
    {MODEL: {"hit": [], "miss": 4, "output": 2}},
])
def test_missing_or_bad_prices_do_not_create_fake_zero_cost(tmp_path: Path, prices: dict) -> None:
    table = PriceTable(price_file(tmp_path, prices))
    assert table.cost(usage_event()) is None


def test_session_cost_includes_mixed_models_compaction_and_explorer(tmp_path: Path) -> None:
    table = PriceTable(price_file(tmp_path))
    store = SessionStore(tmp_path / "sessions", prices=table)
    session = store.create(tmp_path, "auto", MODEL, "环境")
    session.append(usage_event())
    session.append({**usage_event(), "purpose": "compaction", "step": 0})
    session.append({**usage_event("test-fast-model"), "purpose": "explore", "step": 0})
    assert session.cost == pytest.approx(0.00345 * 2 + 0.0053)
    assert session.summary()["cost"] == pytest.approx(session.cost)
    assert session.summary()["budget"] is None
    restored = SessionStore(store.root, prices=table).get(session.id)
    assert restored is not None and restored.cost == pytest.approx(session.cost)


def test_mixed_known_and_unknown_usage_has_unknown_total_and_no_fake_yuan(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions", prices=PriceTable(price_file(tmp_path)))
    session = store.create(tmp_path, "auto", MODEL, "")
    session.append(usage_event())
    session.append(usage_event("unknown"))
    assert session.cost is None and session.summary()["cost"] is None
    lines = usage_lines(session.summary())
    assert any("输入" in line and "token" in line for line in lines)
    assert not any("¥" in line for line in lines)
    console = recording()
    renderer = Renderer(console)
    renderer.event({"type": "user", "text": "做事"})
    renderer.event(usage_event())
    renderer.event({"type": "turn_end", "reason": "done", "cost": None})
    assert "¥" not in console.export_text()


@pytest.mark.anyio
async def test_unknown_usage_does_not_reset_accumulated_priced_usage_after_reload(tmp_path: Path) -> None:
    model = scripted(["不会执行。", STEP_USAGE])
    rt = runtime(tmp_path, model)
    session = rt.create(str(tmp_path), "auto", budget=0.01)
    session.append(usage_event(cache_hit=0, cache_miss=3000, prompt=3000, completion=0))
    session.append(usage_event("unknown"))
    await rt.shutdown()
    reopened = runtime(tmp_path, model)
    try:
        assert reopened.store.get(session.id).cost is None
        events = await run_turn(reopened, session.id)
        assert events[-1]["reason"] == "budget" and model.requests == []
    finally:
        await reopened.shutdown()


@pytest.mark.anyio
async def test_budget_stops_before_fourth_step_and_preserves_legal_messages(tmp_path: Path) -> None:
    model = scripted(*([[call("list_files"), STEP_USAGE]] * 5))
    rt = runtime(tmp_path, model)
    session = rt.create(str(tmp_path), "auto", budget=0.01)
    try:
        events = await run_turn(rt, session.id)
        assert events[-1]["reason"] == "budget"
        assert len(model.requests) == 3
        assert session.cost == pytest.approx(0.012) and events[-1]["cost"] == pytest.approx(0.012)
        assert session.budget == pytest.approx(0.01)
        assert session.unanswered_calls() == []
        messages = session.messages(rt.agent(session.id).system)
        calls = {call_["id"] for message in messages for call_ in message.get("tool_calls", [])}
        replies = {message["tool_call_id"] for message in messages if message["role"] == "tool"}
        assert calls == replies
        requests_before = len(model.requests)
        retry = await run_turn(rt, session.id)
        assert retry[-1]["reason"] == "budget" and retry[-1]["cost"] == 0
        assert len(model.requests) == requests_before
    finally:
        await rt.shutdown()


@pytest.mark.anyio
async def test_raise_budget_resume_and_null_budget_is_unlimited(tmp_path: Path) -> None:
    model = scripted(*([[call("list_files"), STEP_USAGE]] * 3), ["这一轮做完了。", STEP_USAGE])
    rt = runtime(tmp_path, model)
    session = rt.create(str(tmp_path), "auto", budget=0.01)
    try:
        assert (await run_turn(rt, session.id))[-1]["reason"] == "budget"
        previous = session.messages(rt.agent(session.id).system)
        previous_log = session.path.read_bytes()
        budget = rt.set_budget(session.id, 0.02)
        assert budget["type"] == "budget" and budget["amount"] == 0.02
        assert session.path.read_bytes().startswith(previous_log)
        assert session.messages(rt.agent(session.id).system) == previous
        done = await run_turn(rt, session.id)
        assert done[-1]["reason"] == "done" and done[-1]["cost"] == pytest.approx(0.004)
        assert session.cost == pytest.approx(0.016)
        rt.set_budget(session.id, None)
        assert session.budget is None and session.summary()["budget"] is None
        assert (await run_turn(rt, session.id))[-1]["reason"] == "done"
        assert SessionStore(rt.store.root).get(session.id).budget is None
    finally:
        await rt.shutdown()


@pytest.mark.anyio
async def test_budget_counts_real_explorer_and_stops_before_next_main_request(tmp_path: Path) -> None:
    main = scripted([call("explore", task="查清楚项目"), STEP_USAGE], ["不会执行", STEP_USAGE])
    fast = scripted(["查到了。", STEP_USAGE], name="test-fast-model")
    price_file(tmp_path)
    rt = Runtime(tmp_path / "data", lambda: main, shell=SHELL, fast_factory=lambda: fast)
    session = rt.create(str(tmp_path), "auto", budget=0.009)
    try:
        events = await run_turn(rt, session.id)
        assert events[-1]["reason"] == "budget"
        assert len(main.requests) == len(fast.requests) == 1
        assert any(e["type"] == "usage" and e.get("purpose") == "explore" for e in events)
        assert session.cost == pytest.approx(0.01)
    finally:
        await rt.shutdown()


@pytest.mark.anyio
async def test_budget_checks_before_and_after_compaction(tmp_path: Path) -> None:
    model = scripted(
        [call("list_files"), STEP_USAGE],
        ["压缩后的摘要", STEP_USAGE],
        ["不应请求第三次", STEP_USAGE],
    )
    rt = runtime(tmp_path, model, config=AgentConfig(context_window=10, compact_at=0.5))
    session = rt.create(str(tmp_path), "auto", budget=0.008)
    try:
        events = await run_turn(rt, session.id)
        assert events[-1]["reason"] == "budget" and len(model.requests) == 2
        assert any(e["type"] == "usage" and e.get("purpose") == "compaction" for e in events)
        assert events[-1]["cost"] == pytest.approx(0.008)
        count = len(model.requests)
        assert (await run_turn(rt, session.id))[-1]["reason"] == "budget"
        assert len(model.requests) == count
    finally:
        await rt.shutdown()


@pytest.mark.anyio
async def test_unknown_model_never_reports_zero_and_budget_zero_makes_no_request(tmp_path: Path) -> None:
    unknown = scripted(["未计价模型的回答", STEP_USAGE], name="not-priced")
    rt = runtime(tmp_path, unknown)
    session = rt.create(str(tmp_path), "auto", budget=0.01)
    zero = rt.create(str(tmp_path), "auto", budget=0)
    try:
        events = await run_turn(rt, session.id)
        assert events[-1]["reason"] == "done" and events[-1]["cost"] is None
        assert session.cost is None and len(unknown.requests) == 1
        assert (await run_turn(rt, zero.id))[-1]["reason"] == "budget"
        assert len(unknown.requests) == 1
    finally:
        await rt.shutdown()


@pytest.mark.parametrize("value", [-1, True, "0.5", float("nan"), float("inf"), float("-inf")])
def test_budget_validation_rejects_bad_values_without_appending(tmp_path: Path, value: Any) -> None:
    with pytest.raises((ValueError, TypeError)):
        validate_budget(value)
    rt = runtime(tmp_path, scripted(["好。"]))
    session = rt.create(str(tmp_path), "auto")
    old_log = session.path.read_bytes()
    with pytest.raises(RuntimeError_):
        rt.set_budget(session.id, value)
    with pytest.raises(RuntimeError_):
        rt.create(str(tmp_path), "auto", budget=value)
    assert session.path.read_bytes() == old_log and session.budget is None


def test_missing_price_file_and_null_budget_are_supported(tmp_path: Path) -> None:
    assert validate_budget(None) is None
    path = tmp_path / "unavailable-prices.json"
    assert PriceTable(path).cost(usage_event()) is None
    path.write_text("{", encoding="utf-8")
    assert PriceTable(path).cost(usage_event()) is None


def test_budget_api_create_update_clear_missing_and_invalid_input(tmp_path: Path) -> None:
    rt = runtime(tmp_path, scripted(["好。"]))
    app.dependency_overrides[get_runtime] = lambda: rt
    try:
        from fastapi.testclient import TestClient
        client = TestClient(app)
        created = client.post("/agent/sessions", json={"cwd": str(tmp_path), "budget": 0.5})
        assert created.status_code == 201 and created.json()["budget"] == 0.5
        sid = created.json()["id"]
        path = f"/agent/sessions/{sid}/budget"
        assert client.put(path, json={"amount": 1}).json()["amount"] == 1
        assert client.get(f"/agent/sessions/{sid}").json()["budget"] == 1
        assert client.put(path, json={"amount": None}).json()["amount"] is None
        assert client.get("/agent/sessions").json()[0]["budget"] is None
        assert client.put("/agent/sessions/20990101-000000-abcdef/budget", json={"amount": 1}).status_code == 404
        assert client.put(path, json={"amount": 1}, headers={"Origin": "https://example.com"}).status_code == 403
        assert client.put(path, json={}).status_code == 422
        for value in (-1, True, "NaN", "0.5"):
            assert client.put(path, json={"amount": value}).status_code == 422
            assert client.post("/agent/sessions", json={"cwd": str(tmp_path), "budget": value}).status_code == 422
        for value in ("NaN", "Infinity", "-Infinity"):
            assert client.put(path, content='{"amount":' + value + '}', headers={"content-type": "application/json"}).status_code == 422
            body = '{"cwd":' + json.dumps(str(tmp_path)) + ',"budget":' + value + '}'
            assert client.post("/agent/sessions", content=body, headers={"content-type": "application/json"}).status_code == 422
    finally:
        app.dependency_overrides.pop(get_runtime, None)


@pytest.mark.anyio
@pytest.mark.parametrize("remote", [False, True])
async def test_cli_backend_create_set_budget_usage_and_resume(tmp_path: Path, remote: bool) -> None:
    model = scripted(["好。", STEP_USAGE])
    rt = runtime(tmp_path, model)
    app.dependency_overrides[get_runtime] = lambda: rt
    backend = HttpBackend("http://testserver", transport=httpx.ASGITransport(app=app)) if remote else EmbeddedBackend(rt)
    try:
        args = parse_args(["--cwd", str(tmp_path), "--budget", "0.5"])
        info, resumed = await open_session(backend, args, recording())
        assert not resumed and info["budget"] == 0.5
        await run_turn(rt, info["id"])
        with create_pipe_input() as inp:
            console = recording()
            term = Terminal(backend, console, info, inp=inp, output=DummyOutput())
            await term.command("/budget 1")
            assert (await backend.session(info["id"]))["budget"] == 1
            await term.command("/budget off")
            assert (await backend.session(info["id"]))["budget"] is None
            for invalid in ("nan", "inf", "-1", "true"):
                await term.command("/budget " + invalid)
                assert (await backend.session(info["id"]))["budget"] is None
            await term.command("/usage")
            output = console.export_text()
            assert "已花 ¥0.004000" in output and "花费不限" in output
        resume_args = parse_args(["--cwd", str(tmp_path), "-c", "--budget", "2"])
        opened, resumed = await open_session(backend, resume_args, recording())
        assert resumed and opened["id"] == info["id"] and opened["budget"] == 2
    finally:
        app.dependency_overrides.pop(get_runtime, None)
        await backend.close()
        await rt.shutdown()


@pytest.mark.anyio
async def test_headless_budget_exit_and_json_events(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    model = scripted(*([[call("list_files"), STEP_USAGE]] * 4))
    rt = runtime(tmp_path, model)
    backend = EmbeddedBackend(rt)
    try:
        info = await backend.create(str(tmp_path), "auto", budget=0.01)
        code = await headless(backend, info["id"], "列文件", json_lines=True)
        assert code == 1
        events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert events[-1]["reason"] == "budget" and events[-1]["cost"] == pytest.approx(0.012)
        console = recording()
        Renderer(console).event(events[-1])
        output = console.export_text()
        assert "花到上限" in output and "¥0.012000" in output
    finally:
        await backend.close()


def test_cli_budget_argument_validation() -> None:
    assert parse_args(["--budget", "0"]).budget == 0
    assert parse_args([]).budget is None
    for value in ("-1", "nan", "inf", "-inf", "true", "乱写"):
        with pytest.raises(SystemExit) as error:
            parse_args(["--budget", value])
        assert error.value.code == 2


def test_small_positive_cost_is_not_displayed_as_zero() -> None:
    summary = {"usage": {"prompt": 1}, "cost": 0.0000005}
    assert "已花不到 ¥0.000001" in usage_lines(summary)
    console = recording()
    Renderer(console).event({"type": "turn_end", "reason": "done", "cost": summary["cost"]})
    output = console.export_text()
    assert "不到 ¥0.000001" in output and "¥0.000000" not in output
    assert "已花 ¥0.000000" in usage_lines({"cost": 0})
