"""用厂商返回的字段形状模拟额度读取，不使用本机登录或网络。"""

import asyncio
import json
import shlex
import subprocess
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes import quotas as routes
from app.runtime import quota_providers as vendor
from app.runtime.quotas import QuotaBoard, Sample, estimate

NOW = 1_800_000_000
END = NOW + 7200


def test_providers_keep_bucket_units_and_reset_boundaries():
    codex = vendor.codex({
        "rateLimits": {"limitId": "legacy", "primary": {"usedPercent": 99}},
        "rateLimitsByLimitId": {
            "codex": {
                "primary": {"usedPercent": 30, "resetsAt": END, "windowDurationMins": 300},
                "secondary": {"usedPercent": 20, "resetsAt": END + 7200, "windowDurationMins": 10080},
                "credits": {"balance": "125.50", "unlimited": False},
            },
            "review": {"limitName": "Reviews", "primary": {"usedPercent": 2}},
        },
    })
    assert [(w.id, w.remaining, w.unit) for w in codex] == [
        ("codex/primary", 70, "%"), ("codex/secondary", 80, "%"),
        ("codex/credits", 125.5, "credits"), ("review/primary", 98, "%"),
    ]
    assert (codex[0].reset_at, codex[0].minutes, codex[-1].reset_at) == (END, 300, None)
    claude = vendor.claude({
        "rate_limits": {"five_hour": {"used_percentage": 0, "resets_at": END},
                        "seven_day": {"used_percentage": 107, "resets_at": END}},
        "spend_limit": {"used_usd": 10, "limit_usd": 15, "period": "monthly"},
    })
    assert [(w.remaining, w.total, w.unit) for w in claude] == [(100, 100, "%"), (0, 100, "%"), (5, 15, "USD")]
    agy = vendor.antigravity({"command": {"data": {"groups": [
        {"name": "Gemini", "buckets": [
            {"id": "weekly", "remaining_fraction": 0.25, "window": "weekly", "reset_time": "2026-11-27T05:00:00Z"},
            {"id": "invalid", "remaining_fraction": -1},
        ]},
    ]}}})
    assert [(w.id, w.remaining, w.minutes, w.reset_at) for w in agy] == [
        ("Gemini/weekly", 25, 10080, 1795755600),
    ]
    grok = vendor.grok({"config": {
        "creditUsagePercent": 40, "isUnifiedBillingUser": True,
        "currentPeriod": {"type": "USAGE_PERIOD_TYPE_WEEKLY", "end": "2026-11-27T05:00:00Z"},
        "prepaidBalance": {"val": 1250},
    }})
    assert [(w.remaining, w.unit, w.shared) for w in grok] == [(60, "%", True), (12.5, "USD", True)]
    assert grok[0].reset_at == 1795755600
    assert vendor.grok({"config": {"used": {"val": 500}, "monthlyLimit": {"val": 3000}}}) == [
        vendor.Window("included", "月额度", 25, 30, "USD"),
    ]


def test_forecast_requires_same_cycle_and_consumption():
    def reading(at, remaining, reset=END):
        return Sample(at, [vendor.Window("weekly", "额度", remaining, 100, "%", reset)])

    first, second = reading(NOW, 75), reading(NOW + 600, 50)
    assert estimate([first], first.windows[0], NOW) is None
    assert estimate([first, second], second.windows[0], second.at) == {
        "at": NOW + 1800, "within_cycle": True,
    }
    assert estimate([first, second], second.windows[0], END) is None
    topup = reading(NOW + 1200, 75)
    assert estimate([first, second, topup], topup.windows[0], topup.at) is None
    reset = reading(NOW + 1800, 25, END + 7200)
    assert estimate([first, second, reset], reset.windows[0], reset.at) is None
    nearly_full = reading(NOW + 600, 74)
    assert estimate([first, nearly_full], nearly_full.windows[0], nearly_full.at)["within_cycle"] is False


def test_board_uses_real_capture_time_and_only_confirmed_shared_windows(monkeypatch, tmp_path):
    now = NOW
    runs = []

    async def collect(key, _path):
        runs.append(key)
        if key == "grok":
            return "ready", [
                vendor.Window("included", "pool", 60, 100, "%", END, shared=True),
                vendor.Window("extra", "balance", 5, unit="USD"),
            ]
        return "unavailable", []

    monkeypatch.setattr(vendor, "collect", collect)
    path = tmp_path / "usage"
    board = QuotaBoard(path, lambda: now)

    async def check():
        first = await board.refresh()
        assert next(p for p in first if p["id"] == "cursor")["windows"] == []
        cursor = next(p for p in await board.link_cursor(True) if p["id"] == "cursor")
        assert [(w["remaining"], w["source"]) for w in cursor["windows"]] == [(60, "shared")]
        await board.refresh()
        assert len(runs) == 3

    asyncio.run(check())
    assert next(p for p in QuotaBoard(path, lambda: now + 4000).view() if p["id"] == "cursor")["windows"][0]["stale"]


def test_second_codex_quota_has_its_own_history(monkeypatch, tmp_path):
    async def collect(key, _path):
        amount = {"codex": 81, "codex-alt": 31}.get(key)
        return ("ready", [vendor.Window("usage", "Codex", amount, 100, "%", END)]) if amount else ("unavailable", [])

    monkeypatch.setattr(vendor, "collect", collect)
    path = tmp_path / "provider-quotas"
    board = QuotaBoard(path, lambda: NOW)
    assert "codex-alt" not in {p["id"] for p in board.view()}
    asyncio.run(board.refresh())
    (tmp_path / "agent-accounts" / "codex-alt").mkdir(parents=True)
    asyncio.run(board.refresh())
    readings = {p["id"]: p["windows"][0]["remaining"] for p in QuotaBoard(path, lambda: NOW).view() if p["windows"]}
    assert readings == {"codex": 81, "codex-alt": 31}


def test_manual_credit_records_are_editable_persistent_and_independent(tmp_path):
    now = NOW
    board = QuotaBoard(tmp_path, lambda: now)

    async def check():
        nonlocal now
        p = (await board.record(None, "测试平台", None, "积分", 1000, None, "点", END))[-1]
        key, window = p["id"], p["windows"][0]["id"]
        await board.record(key, "", None, "赠送余额", 37, 40, "点", None)
        for _ in range(9):
            now += 600
            await board.record(key, "", window, "积分", 1000 - (now - NOW) / 6, None, "点", END)
        return key

    key = asyncio.run(check())
    saved = QuotaBoard(tmp_path, lambda: now).view()[-1]
    assert saved["id"] == key and saved["name"] == "测试平台"
    by_name = {row["label"]: row for row in saved["windows"]}
    assert by_name["积分"]["remaining"] == 100
    assert by_name["积分"]["forecast"]["at"] == now + 600
    assert by_name["赠送余额"]["remaining"] == 37


def test_api_validates_and_records_without_requesting_provider_data(monkeypatch, tmp_path):
    app = FastAPI()
    app.include_router(routes.router)
    board = QuotaBoard(tmp_path, lambda: NOW)
    app.dependency_overrides[routes.get_board] = lambda: board
    monkeypatch.setattr(vendor, "collect", lambda *_: pytest.fail("No CLI for a manual record"))
    with TestClient(app) as client:
        assert len(client.get("/agent/quotas").json()) == 5
        record = {"provider": "cursor", "label": "其他模型", "unit": "%", "remaining": 42, "total": 100}
        assert client.post("/agent/quotas/record", json={**record, "remaining": 101}).status_code == 422
        assert client.post("/agent/quotas/record", json={**record, "remaining": "42"}).status_code == 422
        result = client.post("/agent/quotas/record", json=record)
        assert result.status_code == 200
        assert result.json()[2]["windows"][0]["remaining"] == 42


def test_stdio_requests_wait_for_initialization_and_use_only_quota_method(tmp_path):
    script = """
import json, sys
read = lambda: json.loads(sys.stdin.readline())
init = read()
assert init["method"] == "initialize"
print("CLI startup")
print(json.dumps({"id": init["id"], "result": {}}))
assert read()["method"] == "initialized"
request = read()
assert request["method"] == "account/rateLimits/read"
assert "params" not in request
print(json.dumps({"method": "unrelated"}))
print(json.dumps({"id": request["id"], "result": {"ready": True}}))
"""
    data = asyncio.run(vendor._rpc(
        [sys.executable, "-u", "-c", script], tmp_path, {"clientInfo": {}},
        "account/rateLimits/read", initialized_notice=True,
    ))
    assert data == {"ready": True}


def test_claude_statusline_updates_existing_file_and_drops_unrelated_fields(tmp_path):
    folder = tmp_path / "quota with spaces"
    board = QuotaBoard(folder, lambda: NOW)
    config = board.claude_config()
    assert config["statusLine"]["type"] == "command"
    args = shlex.split(config["statusLine"]["command"])
    ps = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", config["powerShell"]] if sys.platform == "win32" else args
    result_file = folder / "claude-quota.json"
    for used, cmd in ((20, args), (38, ps)):
        payload = {"rate_limits": {"five_hour": {"used_percentage": used, "resets_at": END, "extra": "ignore"}},
                   "workspace": {"current_dir": "not needed"}, "session_id": "not needed"}
        result = subprocess.run(cmd, input=json.dumps(payload), text=True, capture_output=True, timeout=10)
        assert result.returncode == 0, result.stderr
        snapshot = json.loads(result_file.read_bytes())
        assert snapshot["rate_limits"] == {"five_hour": {"used_percentage": used, "resets_at": END}}
        assert set(snapshot) == {"rate_limits", "spend_limit"}
