"""按本机 Agent 分组的模型目录。启动空 ACP 会话读取选项，并缓存结果以便任务页快速打开。"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, Callable

import acp
from acp import schema

from .localagents import CHAT_ENGINES, ENGINES, AdapterMissing, LocalAgent, LocalAgentError, detect, launch

log = logging.getLogger("aila.catalog")

STALE = 24 * 3600  # 秒：记下的模型列表过了这么久就在后台再问一遍
PROBE_TIMEOUT = 60
OWN = "aila"  # 艾拉自己（你填的大模型）


async def probe_models(engine_id: str, data_dir: Path, launcher: Callable[..., Any] = launch,
                       spawn: Callable[..., Any] = acp.spawn_agent_process) -> list[dict[str, str]]:
    """连上它、开一个空会话，读它给的模型选项（不发话）"""
    from .external import _options_of

    node, args, env = await asyncio.to_thread(launcher, engine_id, data_dir)
    home = data_dir / "model-probe"  # 空文件夹：开会话要一个目录
    home.mkdir(parents=True, exist_ok=True)

    class _Quiet:
        async def session_update(self, *a: Any, **k: Any) -> None:
            pass

        async def request_permission(self, *a: Any, **k: Any) -> Any:
            return schema.RequestPermissionResponse(outcome=schema.DeniedOutcome(outcome="cancelled"))

        async def ext_method(self, method: str, params: dict) -> dict:
            return {}

        async def ext_notification(self, method: str, params: dict) -> None:
            pass

    async with AsyncExitStack() as stack:
        conn, _ = await stack.enter_async_context(spawn(lambda _a: _Quiet(), node, *args, env=env, cwd=str(home)))
        await asyncio.wait_for(conn.initialize(protocol_version=acp.PROTOCOL_VERSION, client_capabilities=schema.ClientCapabilities(),
                                               client_info=schema.Implementation(name="aila", version="0.3")), PROBE_TIMEOUT)
        res = await asyncio.wait_for(conn.new_session(cwd=str(home), mcp_servers=[]), PROBE_TIMEOUT)
        model = next((o for o in _options_of(getattr(res, "config_options", None)) if o["category"] == "model"), None)
        if model is None:
            return []
        return [{"value": c["value"], "name": c.get("name") or c["value"], "description": c.get("description", ""),
                 **({"default": True} if c["value"] == model["current"] else {})} for c in model["choices"]]


class ModelCatalog:
    def __init__(self, data_dir: Path, own: Callable[[str], dict[str, Any]],
                 detector: Callable[[Path], list[LocalAgent]] = detect,
                 prober: Callable[[str, Path], Any] = probe_models) -> None:
        """own(use)：艾拉自己的那组（use 是 chat 或 task：对话和编程用的模型可以不一样）"""
        self.data_dir = data_dir
        self._own = own
        self._detect = detector
        self._probe = prober
        self._path = data_dir / "models.json"
        self._cache: dict[str, dict[str, Any]] = self._load()
        self._probing: dict[str, asyncio.Task[None]] = {}
        self._errors: dict[str, str] = {}

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._cache, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError as err:
            log.warning("模型列表存不下来：%s", err)

    async def groups(self, use: str = "task", refresh: bool = False) -> list[dict[str, Any]]:
        """按账号分组的模型。本机 Agent 的列表要问它的，先给记下的，在后台问（loading 为真时等一会儿再来拿）"""
        agents = await asyncio.to_thread(self._detect, self.data_dir)
        out = [self._own(use)]
        for agent in agents:
            if use == "chat" and agent.id not in CHAT_ENGINES:
                continue
            cached = self._cache.get(agent.id) or {}
            stale = time.time() - float(cached.get("at", 0)) > STALE
            if agent.ready and not agent.installs_adapter and (refresh or (stale and agent.id not in self._errors)):
                self._start_probe(agent.id)
            models = list(cached.get("models") or [])
            if not models:
                # 还不知道有哪些（还没装适配器、还在问）：先给一个「默认模型」，选它就用它自己默认的
                models = [{"value": "", "name": "默认模型", "description": "用它自己默认的模型", "default": True}]
            out.append({
                "engine": agent.id, "title": agent.account or agent.title, "agent": agent.title,
                "ready": agent.ready, "logged_in": agent.logged_in, "problem": agent.problem or self._errors.get(agent.id, ""),
                "hints": agent.hints, "can_install": not agent.path, "can_login": agent.can_login,
                "installs_adapter": agent.installs_adapter,
                "loading": agent.id in self._probing, "models": models,
            })
        return out

    def _start_probe(self, engine_id: str) -> None:
        if engine_id in self._probing:
            return
        task = asyncio.ensure_future(self._probe_one(engine_id))
        self._probing[engine_id] = task
        task.add_done_callback(lambda _t: self._probing.pop(engine_id, None))

    async def _probe_one(self, engine_id: str) -> None:
        try:
            models = await asyncio.wait_for(self._probe(engine_id, self.data_dir), PROBE_TIMEOUT * 2)
        except AdapterMissing:
            return
        except (LocalAgentError, acp.RequestError, OSError, TimeoutError, EOFError, ConnectionError) as err:
            title = ENGINES[engine_id].title if engine_id in ENGINES else engine_id
            self._errors[engine_id] = f"问 {title} 有哪些模型时出错了：{str(err)[:160]}"
            log.info("问 %s 的模型出错：%s", engine_id, err)
            return
        except Exception as err:  # 适配器的毛病不能把列表也弄坏
            self._errors[engine_id] = f"问有哪些模型时出错了：{str(err)[:160]}"
            log.warning("问 %s 的模型出错", engine_id, exc_info=True)
            return
        self._errors.pop(engine_id, None)
        self._cache[engine_id] = {"models": models, "at": time.time()}
        self._save()

    async def wait(self) -> None:
        """测试用：等后台在问的都问完"""
        while self._probing:
            await asyncio.gather(*list(self._probing.values()), return_exceptions=True)

    async def close(self) -> None:
        for task in list(self._probing.values()):
            task.cancel()
        await asyncio.gather(*list(self._probing.values()), return_exceptions=True)


__all__ = ["OWN", "ModelCatalog", "probe_models"]
