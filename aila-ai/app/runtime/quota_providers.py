"""只通过各厂商本机 CLI 的用量接口取读数，统一保留每个额度窗口自己的单位。"""

import asyncio
import json
import math
import os
import shutil
import subprocess
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from .localagents import ENGINES, account_env, account_provider, find_program

PROVIDERS = {
    "codex": "Codex", "claude": "Claude Code", "cursor": "Cursor",
    "grok": "Grok", "antigravity": "Antigravity",
}
CLI_PROVIDERS = ("codex", "grok", "antigravity")


@dataclass(frozen=True)
class Window:
    id: str
    label: str
    remaining: float
    total: float | None = None
    unit: str = "%"
    reset_at: float | None = None
    minutes: int | None = None
    shared: bool = False


def obj(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def rows(value: object) -> list:
    return value[:100] if isinstance(value, list) else []


def label(value: object, fallback: str) -> str:
    return value.strip()[:100] if isinstance(value, str) and value.strip() else fallback


def number(value: object) -> float | None:
    if type(value) in (int, float):
        try:
            n = float(value)
            if math.isfinite(n):
                return n
        except OverflowError:
            pass
    return None


def timestamp(value: object) -> float | None:
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            value = dt.timestamp() if dt.tzinfo is not None else None
        except (ValueError, OverflowError, OSError):
            return None
    n = number(value)
    return n if n is not None and 0 < n <= 253402300799 else None


def percent(key: str, name: str, used: object, reset: object, minutes: int | None = None) -> Window | None:
    value = number(used)
    if value is None or value < 0:
        return None
    return Window(key, name, max(0, 100 - value), 100, "%", timestamp(reset), minutes)


def codex(data: dict) -> list[Window]:
    legacy = obj(data.get("rateLimits"))
    buckets = obj(data.get("rateLimitsByLimitId")) or {label(legacy.get("limitId"), "codex"): legacy}
    result = []
    read_balances = set()
    for key, raw in list(buckets.items())[:30]:
        bucket = obj(raw)
        name = label(bucket.get("limitName"), "Codex" if key == "codex" else key)
        for kind in ("primary", "secondary"):
            window = obj(bucket.get(kind))
            duration = number(window.get("windowDurationMins"))
            mins = int(duration) if duration and 0 < duration <= 525600 else None
            item = percent(f"{key}/{kind}", name, window.get("usedPercent"), window.get("resetsAt"), mins)
            if item is not None:
                result.append(item)
        # 同一份积分余额可能出现在多个额度窗口里。
        credits = obj(bucket.get("credits"))
        balance = credits.get("balance")
        if isinstance(balance, str) and len(balance) < 80:
            try:
                balance = float(balance)
            except ValueError:
                balance = None
        value = number(balance)
        if credits.get("unlimited") is not True and value is not None and value >= 0 and value not in read_balances:
            result.append(Window(f"{key}/credits", name, value, unit="credits"))
            read_balances.add(value)
    return result


def claude(data: dict) -> list[Window]:
    result = []
    limits = obj(data.get("rate_limits"))
    for key, mins in (("five_hour", 300), ("seven_day", 10080)):
        raw = obj(limits.get(key))
        item = percent(key, "Claude Code", raw.get("used_percentage"), raw.get("resets_at"), mins)
        if item is not None:
            result.append(item)
    spend = obj(data.get("spend_limit"))
    used, limit = number(spend.get("used_usd")), number(spend.get("limit_usd"))
    if used is not None and used >= 0 and limit is not None and limit > 0:
        window_label = {"daily": "日上限", "weekly": "周上限", "monthly": "月上限"}.get(label(spend.get("period"), ""), "消费上限")
        result.append(Window("spend", window_label, max(0, limit - used), limit, "USD", timestamp(spend.get("resets_at"))))
    return result


def antigravity(data: dict) -> list[Window]:
    result = []
    groups = obj(obj(data.get("command")).get("data")).get("groups")
    for group in map(obj, rows(groups)):
        name = label(group.get("name"), "Antigravity")
        for raw in map(obj, rows(group.get("buckets"))):
            key, fraction = raw.get("id"), number(raw.get("remaining_fraction"))
            if not isinstance(key, str) or not key or fraction is None or not 0 <= fraction <= 1:
                continue
            period = label(raw.get("window"), "")
            duration = {"daily": 1440, "weekly": 10080}.get(period.lower())
            result.append(Window(f"{name}/{key}", name, fraction * 100, 100, "%", timestamp(raw.get("reset_time")), duration))
    return result


def grok(data: dict) -> list[Window]:
    config = obj(data.get("config"))
    current = obj(config.get("currentPeriod"))
    period = label(current.get("type"), "")
    reset = timestamp(current.get("end")) or timestamp(config.get("billingPeriodEnd"))
    shared = config.get("isUnifiedBillingUser") is True
    name = "共享额度" if shared else "Grok Build"
    mins = 10080 if period.endswith("_WEEKLY") else None
    if period.endswith("_MONTHLY"):
        name = "月额度" if name == "Grok Build" else "共享月额度"

    def cents(raw: object) -> float | None:
        value = number(raw.get("val", 0)) if isinstance(raw, dict) else None
        return None if value is None or value < 0 else value / 100

    item = percent("included", name, config.get("creditUsagePercent"), reset, mins)
    used, limit = cents(config.get("used")), cents(config.get("monthlyLimit"))
    if item is None and used is not None and limit is not None and limit > 0:
        item = Window("included", "月额度", max(0, limit - used), limit, "USD", reset)
    if item and shared:
        item = replace(item, shared=True)
    result = [item] if item else []
    balance = cents(config.get("prepaidBalance"))
    if balance is not None:
        result.append(Window("prepaid", "预付余额", balance, unit="USD", shared=shared))
    return result


async def _stdout(proc: asyncio.subprocess.Process) -> AsyncIterator[dict]:
    stdout = proc.stdout
    assert stdout is not None
    while line := await stdout.readline():
        try:
            data = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if isinstance(data, dict):
            yield data


async def _rpc(args: list[str], cwd: Path, initialize: dict, method: str, *,
               initialized_notice: bool = False, query_params: dict | None = None, env: dict[str, str] | None = None) -> dict:
    proc = await asyncio.create_subprocess_exec(
        *args, cwd=cwd, env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, limit=512 * 1024,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    stdin = proc.stdin
    assert stdin is not None

    async def send(message: dict) -> None:
        stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        await stdin.drain()

    async def read() -> dict:
        await send({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": initialize})
        async for data in _stdout(proc):
            if data.get("id") == 0:
                if "error" in data:
                    return {}
                break
        else:
            return {}
        if initialized_notice:
            await send({"jsonrpc": "2.0", "method": "initialized"})
        request = {"jsonrpc": "2.0", "id": 1, "method": method}
        if query_params is not None:
            request["params"] = query_params
        await send(request)
        async for data in _stdout(proc):
            if data.get("id") == 1:
                return obj(data.get("result"))
        return {}

    try:
        return await asyncio.wait_for(read(), 10)
    finally:
        stdin.close()
        # 关进程时读完管道，避免余下的通知挂住进程退出。
        try:
            if proc.returncode is None:
                proc.kill()
            await asyncio.wait_for(proc.communicate(), 2)
        except (ProcessLookupError, asyncio.TimeoutError):
            pass


async def _agy(program: str, cwd: Path) -> list[Window]:
    proc = await asyncio.create_subprocess_exec(
        program, "-p", "/usage", "--output-format", "json", cwd=cwd,
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, limit=512 * 1024,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        async def read() -> list[Window]:
            async for payload in _stdout(proc):
                windows = antigravity(payload)
                if windows:
                    return windows
            return []

        try:
            return await asyncio.wait_for(read(), 10)
        finally:
            if proc.returncode is None:
                proc.kill()
            await asyncio.wait_for(proc.communicate(), 2)
    except (ProcessLookupError, asyncio.TimeoutError):
        return []


async def collect(provider: str, cwd: Path) -> tuple[str, list[Window]]:
    info = {"name": "aila", "version": "1.0.0"}
    try:
        if provider == "grok":
            program = shutil.which("grok")
            if not program:
                directory = Path(os.environ.get("GROK_BIN_DIR") or Path.home() / ".grok" / "bin")
                candidate = directory / ("grok.exe" if os.name == "nt" else "grok")
                program = str(candidate) if candidate.is_file() else None
        else:
            program = await asyncio.wait_for(asyncio.to_thread(find_program, ENGINES[provider]), 15)
        if not program:
            return "missing_cli", []
        if account_provider(provider) == "codex":
            windows = codex(await _rpc(
                [program, "app-server"], cwd, {"clientInfo": {**info, "title": "Aila"}},
                "account/rateLimits/read", initialized_notice=True, env=account_env(provider, cwd.parent),
            ))
        elif provider == "grok":
            windows = grok(await _rpc(
                [program, "agent", "stdio"], cwd, {"protocolVersion": 1, "clientCapabilities": {}, "clientInfo": info},
                "_x.ai/billing", query_params={},
            ))
        else:
            windows = await _agy(program, cwd)
    except (OSError, ValueError, RecursionError, NotImplementedError, asyncio.TimeoutError):
        return "unavailable", []
    return ("ready" if windows else "unavailable"), windows[:100]
