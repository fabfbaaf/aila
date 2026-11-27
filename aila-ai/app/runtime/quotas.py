"""平台额度的本地读数和燃尽预估，独立于艾拉的任务预算。"""

import asyncio
import json
import os
import shlex
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

from ..code.fix import atomic_write
from . import quota_providers as vendor

MAX_SAMPLES = 64
MIN_OBSERVATION = 300
STALE_AFTER = 3600


@dataclass(frozen=True)
class Sample:
    at: float
    windows: list[vendor.Window]


def estimate(history: list[Sample], window: vendor.Window, now: float) -> dict | None:
    """用同一次重置周期内可比较的读数估算。"""
    last, first_at, first_remaining = history[-1].at, history[-1].at, window.remaining
    if now - last > STALE_AFTER or (window.reset_at is not None and window.reset_at <= now):
        return None
    if not window.remaining:
        return {"at": last, "within_cycle": True}
    previous = window.remaining
    for sample in reversed(history[:-1]):
        if last - sample.at > 7 * 86400:
            break
        earlier = next((w for w in sample.windows if w.id == window.id), None)
        if (earlier is None or earlier.total != window.total or earlier.unit != window.unit
                or earlier.reset_at != window.reset_at or earlier.remaining < previous):
            break  # 额度重置、充值或换单位后，重新收集读数。
        first_at, first_remaining, previous = sample.at, earlier.remaining, earlier.remaining
    drop = first_remaining - window.remaining
    if last - first_at < MIN_OBSERVATION or drop <= 0:
        return None
    empty = last + window.remaining * (last - first_at) / drop
    if not vendor.timestamp(empty):
        return None
    return {"at": empty, "within_cycle": window.reset_at is None or empty < window.reset_at}


def _samples(value: object, cap: int = MAX_SAMPLES, *, single: bool = False) -> list[Sample]:
    samples = []
    for raw in vendor.rows(value)[-cap:]:
        data = vendor.obj(raw)
        at = vendor.timestamp(data.get("at"))
        windows = []
        for item in map(vendor.obj, vendor.rows(data.get("windows"))):
            remaining, total = vendor.number(item.get("remaining")), vendor.number(item.get("total"))
            key, name, unit = (item.get(k) for k in ("id", "label", "unit"))
            if remaining is None or remaining < 0 or not all(isinstance(s, str) for s in (key, name, unit)):
                continue
            minutes = item.get("minutes")
            windows.append(vendor.Window(
                key, name, remaining, total if total and total >= remaining else None,
                unit, vendor.timestamp(item.get("reset_at")), minutes if type(minutes) is int else None,
                item.get("shared") is True,
            ))
        if at and windows and (not single or len(windows) == 1):
            samples.append(Sample(at, windows))
    return sorted(samples, key=lambda sample: sample.at)


def _read(path: Path, limit: int) -> dict:
    try:
        with path.open("rb") as file:
            data = file.read(limit + 1)
        if len(data) <= limit:
            return vendor.obj(json.loads(data))
    except (OSError, ValueError, RecursionError):
        pass
    return {}


class QuotaBoard:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path = path
        self.clock = clock
        saved = _read(path / "board.json", 32 * 1024 * 1024)
        self.names = {k: v for k, v in vendor.obj(saved.get("names")).items() if isinstance(v, str)}
        self.auto = {k: _samples(v) for k, v in vendor.obj(saved.get("auto")).items() if k in vendor.PROVIDERS}
        self.manual = {k: _samples(v, 96, single=True) for k, v in vendor.obj(saved.get("manual")).items()
                       if k in vendor.PROVIDERS or k in self.names}
        self.cursor_linked = saved.get("cursor_linked") is True
        self.status: dict[str, str] = {}
        self.lock = asyncio.Lock()
        self.last_refresh: float | None = None

    def _save(self) -> None:
        data = {"names": self.names, "auto": self.auto, "manual": self.manual, "cursor_linked": self.cursor_linked}
        atomic_write(self.path / "board.json", json.dumps(data, ensure_ascii=False, default=asdict).encode("utf-8"))

    @staticmethod
    def _append(target: dict[str, list[Sample]], key: str, sample: Sample) -> None:
        if not sample.windows:
            return
        history = target.setdefault(key, [])
        if history and sample.at <= history[-1].at:
            return
        history.append(sample)
        del history[:-MAX_SAMPLES]

    def _claude(self) -> bool:
        path = self.path / "claude-quota.json"
        try:
            before = path.stat()
            windows = vendor.claude(_read(path, 65536))
            after = path.stat()
        except OSError:
            return False
        history = self.auto.get("claude") or []
        if not windows or before.st_mtime_ns != after.st_mtime_ns or (history and after.st_mtime <= history[-1].at):
            return False
        self._append(self.auto, "claude", Sample(after.st_mtime, windows))
        return True

    def view(self) -> list[dict]:
        now = self.clock()
        result = []
        for key, name in {**vendor.PROVIDERS, **self.names}.items():
            windows = []
            automatic = self.auto.get(key, [])
            status = self.status.get(key, "ready" if automatic else "waiting")
            if key == "cursor" or key in self.names:
                status = "manual"
            source = "automatic"
            if key == "cursor" and self.cursor_linked:
                source = "shared"
                automatic = [Sample(s.at, [w for w in s.windows if w.shared]) for s in self.auto.get("grok", [])]
                status = self.status.get("grok", "ready") if automatic and automatic[-1].windows else "waiting"
            if automatic:
                windows.extend(self._view_window(w, source, automatic, now) for w in automatic[-1].windows)
            manual: dict[str, list[Sample]] = {}
            for sample in self.manual.get(key, []):
                manual.setdefault(sample.windows[0].id, []).append(sample)
            windows.extend(self._view_window(s[-1].windows[0], "manual", s, now) for s in manual.values())
            result.append({"id": key, "name": name, "status": status, "windows": windows,
                           "linked": key == "cursor" and self.cursor_linked})
        return result

    @staticmethod
    def _view_window(window: vendor.Window, source: str, history: list[Sample], now: float) -> dict:
        at = history[-1].at
        return {
            **asdict(window), "source": source, "observed_at": at,
            "stale": now - at > STALE_AFTER,
            "forecast": estimate(history, window, now),
        }

    async def refresh(self) -> list[dict]:
        async with self.lock:
            self.path.mkdir(parents=True, exist_ok=True)
            changed = self._claude()
            if self.last_refresh is None or self.clock() - self.last_refresh >= 60:
                samples = await asyncio.gather(
                    *(vendor.collect(key, self.path) for key in vendor.CLI_PROVIDERS),
                )
                at = self.clock()
                for key, (status, windows) in zip(vendor.CLI_PROVIDERS, samples, strict=True):
                    self.status[key] = status
                    if windows:
                        self._append(self.auto, key, Sample(at, windows))
                        changed = True
                self.last_refresh = at
            if changed:
                self._save()
            return self.view()

    async def record(self, key: str | None, provider_name: str, window_id: str | None, description: str,
                     remaining: float, total: float | None, unit: str, reset_at: float | None) -> list[dict]:
        async with self.lock:
            if key is None:
                if len(self.names) >= 32 or not provider_name:
                    raise ValueError("平台名称为空或数量已到上限。")
                key = uuid.uuid4().hex
                self.names[key] = provider_name
            elif key not in vendor.PROVIDERS and key not in self.names:
                raise ValueError("找不到这个平台。")
            old = self.manual.get(key, [])
            ids = {s.windows[0].id for s in old}
            if (window_id is None and len(ids) >= 12) or (window_id is not None and window_id not in ids):
                raise ValueError("找不到这条记录，或额度窗口数量已到上限。")
            window = vendor.Window(window_id or uuid.uuid4().hex, description, remaining, total, unit, reset_at)
            sample = Sample(self.clock(), [window])
            # 单独保留每个手动窗口的历史，避免更新一个窗口时把别的删掉。
            matching = [s for s in old if s.windows[0].id == window.id][-7:]
            rest = [s for s in old if s.windows[0].id != window.id]
            self.manual[key] = sorted([*rest, *matching, sample], key=lambda s: s.at)
            try:
                self._save()
            except OSError:
                if not old:
                    self.manual.pop(key, None)
                    self.names.pop(key, None)
                else:
                    self.manual[key] = old
                raise
            return self.view()

    def claude_config(self) -> dict:
        executable = Path(sys.executable).as_posix()
        entry = ["--claude-quota"] if getattr(sys, "frozen", False) else [
            "-I", Path(__file__).with_name("claude_statusline.py").as_posix(),
        ]
        args = [executable, *entry, self.path.resolve().as_posix()]
        config = {"statusLine": {"type": "command", "command": shlex.join(args)}}
        if os.name == "nt":
            quoted = ["'" + arg.replace("'", "''") + "'" for arg in args]
            config["powerShell"] = "& " + " ".join(quoted)
        return config

    async def link_cursor(self, linked: bool) -> list[dict]:
        async with self.lock:
            old, self.cursor_linked = self.cursor_linked, linked
            try:
                self._save()
            except OSError:
                self.cursor_linked = old
                raise
            return self.view()


@lru_cache(maxsize=4)
def board(data_dir: Path) -> QuotaBoard:
    return QuotaBoard(data_dir / "provider-quotas")
