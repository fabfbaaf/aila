"""任务队列、原生会话和多仓库工作区。执行和权限仍由 Runtime 管理，队列只负责谁在什么时候开工。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import median
from typing import TYPE_CHECKING

from ..code.fix import FixError, atomic_write, git, repo_root
from .branch import BranchError, BranchInfo, discard, open_branch
from .external import list_native
from .localagents import ENGINES, LocalAgentError, account_provider
from .session import Event, Session

if TYPE_CHECKING:
    from .runner import Runtime

log = logging.getLogger(__name__)
STATE_FILE = "task-workspace.json"


class TaskWorkError(ValueError):
    """任务管理页可以直接显示的错误。"""


@dataclass
class ModelPick:
    engine: str | None = None
    model: str = ""

    def __post_init__(self) -> None:
        if self.engine is not None and self.engine not in ENGINES:
            raise TaskWorkError("找不到选中的 Agent。")
        if self.engine is None and self.model:
            raise TaskWorkError("艾拉自己的模型要在设置里选。")

    def options(self, session: Session) -> None:
        if self.engine and self.model:
            session.append({"type": "engine_initial_model", "value": self.model})


@dataclass
class QueueItem:
    id: str
    project: str
    title: str
    prompt: str
    dependencies: list[str]
    pick: ModelPick
    fallback: ModelPick | None = None
    isolate: bool = True
    sid: str = ""
    state: str = "queued"
    error: str = ""
    attempts: list[str] = field(default_factory=list)


def project_path(cwd: str) -> Path:
    root = Path(cwd).expanduser().resolve()
    if not root.is_dir():
        raise TaskWorkError("这个项目目录找不到。")
    try:
        return repo_root(root)
    except FixError:
        return root


def _path_key(path: Path | str) -> str:
    return os.path.normcase(str(Path(path)))


def _project_of(session: Session) -> str:
    if session.branch:
        return _path_key(session.branch["repo"])
    return _path_key(project_path(str(session.cwd))) if session.cwd.is_dir() else _path_key(session.cwd)


def _last_turn(session: Session) -> Event | None:
    return next((e for e in reversed(session.events) if e["type"] == "turn_end"), None)


def _work_message(event: Event) -> bool:
    return (event["type"] == "user" and not event.get("imported") and not event.get("review")
            and not event.get("plan_request") and not event.get("command"))


def _evidence(session: Session) -> Event | None:
    """用最近的实际验收。新的实施轮会让上一次验收失效。"""
    for event in reversed(session.events):
        if event["type"] == "task_verdict":
            return event
        if event["type"] == "merge" and (event.get("merged") or any(not test["ok"] for test in event.get("tests", []))):
            return {**event, "accepted": bool(event.get("merged"))}
        if _work_message(event):
            break
    return None


def _observed_model(session: Session, before: int) -> tuple[str, str] | None:
    engine = session.events[0].get("engine") or "aila"
    recorded = next((e for e in reversed(session.events[:before]) if e["type"] == "task_model"), None)
    if recorded:
        return engine, recorded["model"]
    if engine == "aila":
        model = next((e["model"] for e in reversed(session.events[:before]) if _work_message(e) and e.get("model")), "")
        model = model or session.events[0].get("model", "")
        return (engine, model) if model else None
    for event in reversed(session.events[:before]):
        if event["type"] == "engine_options":
            model = next((o["current"] for o in event["options"] if o.get("category") == "model"), None)
            if model:
                return engine, model
    return None


class TaskWork:
    def __init__(self, runtime: Runtime) -> None:
        self.rt = runtime
        self.path = runtime.data_dir / STATE_FILE
        self.limits: dict[str, int] = {}
        self.items: dict[str, QueueItem] = {}
        self.groups: dict[str, dict] = {}
        self.worker: asyncio.Task[None] | None = None
        self._import_lock = asyncio.Lock()
        self._active_projects: dict[str, str] = {}
        if self.path.is_file():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.limits = data["limits"]
            self.groups = data["groups"]
            for row in data["items"]:
                pick = ModelPick(**row.pop("pick"))
                fallback = row.pop("fallback")
                item = QueueItem(**row, pick=pick, fallback=ModelPick(**fallback) if fallback else None)
                self.items[item.id] = item
                if item.state in {"starting", "running"}:
                    item.state, item.error = "stopped", "服务在开工时退出了，可以打开任务检查进展。"

    def _save(self) -> None:
        data = {"limits": self.limits, "items": [asdict(item) for item in self.items.values()], "groups": self.groups}
        atomic_write(self.path, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))

    def start(self) -> None:
        if not self.worker or self.worker.done():
            self.worker = asyncio.create_task(self._work())

    async def stop(self) -> None:
        if self.worker is not None:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
            self.worker = None

    async def _work(self) -> None:
        while True:
            try:
                self.tick()
            except Exception:
                log.exception("任务队列没能推进")
            await asyncio.sleep(1)

    def set_limit(self, cwd: str, limit: int) -> None:
        key = _path_key(project_path(cwd))
        previous = self.limits.get(key)
        self.limits[key] = limit
        try:
            self._save()
        except OSError:
            if previous is None:
                self.limits.pop(key)
            else:
                self.limits[key] = previous
            raise

    def check_capacity(self, session: Session) -> None:
        if session.kind != "project" or not self.limits:
            return
        key = _project_of(session)
        if key in self.limits and self._active(key) >= self.limits[key]:
            raise TaskWorkError("这个项目正在做的任务已到上限，等一个任务停下再开工。")

    def _active(self, key: str) -> int:
        active = set()
        for sid in self.rt._tasks:
            if not self.rt.running(sid):
                self._active_projects.pop(sid, None)
                continue
            session = self.rt.store.get(sid)
            if session is not None and session.kind == "project":
                if sid not in self._active_projects:
                    self._active_projects[sid] = _project_of(session)
                if self._active_projects[sid] == key:
                    active.add(sid)
        return len(active)

    def enqueue(self, cwd: str, title: str, prompt: str, dependencies: list[str],
                pick: ModelPick, fallback: ModelPick | None, isolate: bool) -> QueueItem:
        root = project_path(cwd)
        if isolate:
            repo_root(root)
        if not title.strip() or not prompt.strip():
            raise TaskWorkError("填好任务名和要做的事再排队。")
        if fallback == pick:
            raise TaskWorkError("备用模型要选和主模型不同的账号或模型。")
        for key in dependencies:
            dependency = self.rt.store.get(key) if key not in self.items else None
            if key not in self.items and (dependency is None or dependency.kind != "project"):
                raise TaskWorkError("有个前置任务已经找不到了。")
        # 前置只能引用排队前已经存在的任务，从创建顺序上避免依赖环。
        item = QueueItem(secrets.token_hex(8), str(root), title.strip(), prompt.strip(), list(dict.fromkeys(dependencies)),
                         pick, fallback, isolate)
        self.items[item.id] = item
        try:
            self._save()
        except OSError:
            self.items.pop(item.id)
            raise
        self.start()
        return item

    def cancel(self, key: str) -> None:
        item = self.items.get(key)
        if item is None or item.state != "queued":
            raise TaskWorkError("只有还在排队的任务可以从队列里移走。")
        item.state = "canceled"
        try:
            self._save()
        except OSError:
            item.state = "queued"
            raise

    def _session(self, key: str) -> Session | None:
        """前置任务可以引用队列项或普通会话。换模型以后跟到新的会话。"""
        sid = self.items[key].sid if key in self.items else key
        visited: set[str] = set()
        while sid and sid not in visited:
            visited.add(sid)
            session = self.rt.store.get(sid)
            if session is None:
                return None
            next_sid = next((e["to"] for e in reversed(session.events) if e["type"] == "handoff_out"), None)
            if not next_sid:
                return session
            sid = next_sid
        return None

    def _blocked(self, item: QueueItem) -> list[dict]:
        waiting = []
        for key in item.dependencies:
            session = self._session(key)
            evidence = _evidence(session) if session else None
            if evidence and evidence["accepted"] and session and not self.rt.running(session.id):
                continue
            dep = self.items.get(key)
            status = ("rejected" if evidence and not evidence["accepted"] else
                      "canceled" if dep and dep.state == "canceled" else
                      "missing" if not session and not dep else "pending")
            waiting.append({"id": key, "title": dep.title if dep else session.title if session else key, "status": status})
        return waiting

    def verdict(self, sid: str, accepted: bool) -> None:
        session = self.rt.store.get(sid)
        if session is None or session.kind != "project" or self.rt.running(sid):
            raise TaskWorkError("等这个项目任务停下以后再验收。")
        if _last_turn(session) is None or not any(_work_message(e) for e in session.events):
            raise TaskWorkError("这条任务还没有能验收的实施结果。")
        if self._session(sid) != session:
            raise TaskWorkError("这条任务已经交给别的模型，打开接手的任务验收。")
        event = session.append({"type": "task_verdict", "accepted": accepted})
        self.rt._publish(sid, event)

    def tick(self) -> None:
        """同一个事件循环内先记状态再开工，防止重复派工或超出项目并发数。"""
        changed = False
        for item in self.items.values():
            if item.state in {"running", "review", "stopped"} and item.sid and not self.rt.running(item.sid):
                session = self._session(item.id)
                if session and session.id != item.sid:
                    item.sid = session.id
                    changed = True
                    if self.rt.running(item.sid):
                        continue
                end = _last_turn(session) if session else None
                if item.state == "running" and end and end.get("error_code") == "quota" and item.fallback and len(item.attempts) == 1 and session:
                    key = _path_key(item.project)
                    if self._active(key) < self.limits.get(key, 1):
                        self._launch(item, session)
                    continue
                if end or item.state == "running":
                    state = "review" if end and end.get("reason") == "done" else "stopped"
                    error = (end or {}).get("error", "")
                    if (state, error) != (item.state, item.error):
                        item.state, item.error = state, error
                        changed = True
            if item.state != "queued" or self._blocked(item):
                continue
            key = _path_key(item.project)
            if self._active(key) < self.limits.get(key, 1):
                self._launch(item)
        if changed:
            self._save()

    def _launch(self, item: QueueItem, previous: Session | None = None) -> None:
        from .runner import RuntimeError_

        pick = item.fallback if previous else item.pick
        assert pick is not None
        previous_state = item.state
        item.state = "starting"
        try:
            self._save()
        except OSError:
            item.state = previous_state
            raise
        try:
            if previous is None:
                session = self.rt.create(item.project, "ask", item.isolate, engine=pick.engine)
                session.append({"type": "title", "title": item.title})
            else:
                session = self.rt.handoff(previous.id, pick.engine, pick.model)
            item.sid = session.id
            item.attempts.append(session.id)
            pick.options(session)
            self._save()
            prompt = ("按交接记录接着做，先检查已有改动，避免重复操作。\n\n原任务\n" + item.prompt
                      if previous else item.prompt)
            self.rt.start(session.id, prompt)
            item.state = "running"
        except (RuntimeError_, LocalAgentError, BranchError, FixError, OSError, TaskWorkError) as err:
            item.state, item.error = "stopped", str(err)
        self._save()

    def state(self) -> dict:
        items = []
        for item in self.items.values():
            row = asdict(item)
            session = self._session(item.id)
            evidence = _evidence(session) if session else None
            if evidence and evidence["accepted"]:
                row["state"] = "accepted"
            if session:
                row["sid"] = session.id
            row["waiting"] = self._blocked(item) if item.state == "queued" else []
            items.append(row)
        groups = []
        for group in self.groups.values():
            members = []
            for member in group["members"]:
                session = self._session(member["sid"])
                members.append({**member, "sid": session.id if session else None,
                                "outcome": (_last_turn(session) or {}).get("reason") if session else None})
            integration = self._session(group["integration"]) if group["integration"] else None
            groups.append({**group, "members": members,
                           "integration_result": self._integration_result(integration) if integration else None})
        return {"queue": items, "groups": groups}

    @staticmethod
    def _integration_result(session: Session) -> dict:
        # 显示工具实际返回的退出码。模型写的总结仍在联调会话里看。
        start = next((e["seq"] for e in reversed(session.events) if _work_message(e)), len(session.events))
        commands: dict[str, str] = {}
        results = []
        for event in session.events[start:]:
            if event["type"] == "assistant":
                for call in event.get("tool_calls", []):
                    if call["name"] == "run_command":
                        try:
                            args = json.loads(call["arguments"])
                            commands[call["id"]] = str(args.get("command", ""))[:240]
                        except (ValueError, TypeError, AttributeError):
                            continue
            if event["type"] == "tool" and event.get("name") == "run_command" and "exit_code" in event:
                results.append({"id": event["id"], "command": commands.get(event["id"], ""), "exit_code": event["exit_code"]})
        end = _last_turn(session)
        return {"outcome": end["reason"] if end and end["seq"] > start else None, "commands": results[-10:]}

    def recommend(self, cwd: str) -> dict:
        root = project_path(cwd)
        key = _path_key(root)
        buckets: dict[tuple[str, str], list[tuple[bool, float, int]]] = {}
        for path in self.rt.store.root.glob("*.jsonl"):
            session = self.rt.store.get(path.stem)
            if (session is None or session.kind != "project" or self.rt.running(session.id)
                    or any(e["type"] == "handoff_out" for e in session.events)):
                continue
            evidence = _evidence(session)
            if not evidence or _project_of(session) != key:
                continue
            messages = [e for e in session.events[:evidence["seq"]] if _work_message(e)]
            if not messages:
                continue
            turns: list[tuple[tuple[str, str], float]] = []
            for message, stop in zip(messages, [*(e["seq"] for e in messages[1:]), evidence["seq"]]):
                end = next((e for e in session.events[message["seq"] + 1:stop] if e["type"] == "turn_end"), None)
                if end and (model := _observed_model(session, end["seq"])):
                    turns.append((model, max(0.0, end["t"] - message["t"])))
            models = {model for model, _ in turns}
            if len(models) != 1 or len(turns) != len(messages):
                continue
            # 一条结果对应一个实际用过的模型。耗时只算实际执行的轮次。
            seconds = sum(seconds for _, seconds in turns)
            model = turns[0][0]
            buckets.setdefault(model, []).append((bool(evidence["accepted"]), seconds, len(turns) - 1))
        rows = [{"engine": engine, "model": model, "samples": len(values),
                 "passed": sum(passed for passed, _, _ in values),
                 "seconds": round(median(seconds for _, seconds, _ in values)),
                 "rework": round(sum(rework for _, _, rework in values) / len(values), 1)}
                for (engine, model), values in buckets.items()]
        # 加一个正反样本作平滑，避免只有一次通过的模型压过长期表现好的模型。
        rows.sort(key=lambda row: (-(row["passed"] + 1) / (row["samples"] + 2),
                                   row["rework"], row["seconds"], -row["samples"], row["model"]))
        return {"project": str(root), "limit": self.limits.get(key, 1), "models": rows}

    async def discover(self, engine: str, cwd: str, cursor: str | None) -> dict:
        root = self._native_root(engine, cwd)
        return await list_native(engine, self.rt.data_dir, root, cursor)

    @staticmethod
    def _native_root(engine: str, cwd: str) -> Path:
        if account_provider(engine) not in {"codex", "claude"}:
            raise TaskWorkError("现在可以导入 Codex 和 Claude Code 的会话。")
        root = Path(cwd).expanduser().resolve()
        if not root.is_dir():
            raise TaskWorkError("找不到原会话的工作目录。")
        return root

    async def adopt(self, engine: str, cwd: str, native_id: str, title: str) -> dict:
        root = self._native_root(engine, cwd)
        native_id = native_id.strip()
        if not native_id:
            raise TaskWorkError("选一个会话，或填入原会话编号。")
        async with self._import_lock:
            for path in self.rt.store.root.glob("*.jsonl"):
                session = self.rt.store.get(path.stem)
                if session and session.events[0].get("engine") == engine and any(
                    e["type"] == "engine_session" and e["id"] == native_id for e in session.events
                ):
                    if session.workdir != root:
                        raise TaskWorkError("这个原生会话已在另一个工作目录导入。")
                    return self._import_result(session)
            session = self.rt.create(str(root), "ask", engine=engine)
            session.append({"type": "native_import", "id": native_id})
            session.append({"type": "engine_session", "id": native_id, "engine": engine})
            if title.strip():
                session.append({"type": "title", "title": title.strip()})
            try:
                await self.rt.engine_options(session.id)
            except BaseException:
                await self.rt.delete(session.id)
                raise
            return self._import_result(session)

    @staticmethod
    def _import_result(session: Session) -> dict:
        branch, changes = "", []
        try:
            branch = git(session.workdir, "rev-parse", "--abbrev-ref", "HEAD").strip()
            changes = git(session.workdir, "--no-optional-locks", "status", "--short").splitlines()[:100]
        except FixError:
            pass
        return {"sid": session.id, "cwd": str(session.workdir), "branch": branch, "changes": changes,
                "messages": sum(bool(e.get("imported")) for e in session.events)}

    def create_group(self, title: str, repos: list[str], contract: str, acceptance: str, pick: ModelPick) -> dict:
        roots = list(dict.fromkeys(repo_root(project_path(path)) for path in repos))
        if len(roots) < 2:
            raise TaskWorkError("多仓库任务至少选两个不同的 Git 仓库。")
        common = {_path_key(Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()).resolve())
                  for root in roots}
        if len(common) < len(roots):
            raise TaskWorkError("同一个仓库的两个工作区只需要选一个。")
        key = secrets.token_hex(8)
        workspace = self.rt.work_dir / f"group-{key}"
        members: list[dict] = []
        branches: list[BranchInfo] = []
        try:
            for root in roots:
                info = open_branch(root, workspace)
                branches.append(info)
                session = self.rt.create(str(root), "ask", engine=pick.engine, branch=asdict(info))
                session.append({"type": "title", "title": f"{title} · {root.name}"[:100]})
                pick.options(session)
                members.append({"sid": session.id, "repo": str(root), "branch": info.branch, "worktree": info.worktree})
            group = {"id": key, "title": title, "contract": contract, "acceptance": acceptance,
                     "members": members, "integration": None}
            for member in members:
                session = self.rt.store.get(member["sid"])
                assert session is not None
                session.append({"type": "task_group_brief", "text": self._brief(group)})
            self.groups[key] = group
            self._save()
        except Exception:
            self.groups.pop(key, None)
            for member in members:
                self.rt.store.delete(member["sid"])
            for info in reversed(branches):
                try:
                    discard(info)
                except (OSError, FixError):
                    log.exception("清理没建好的工作区失败")
            raise
        return group

    def group_session(self, key: str, member_id: str | None) -> str:
        group = self.groups.get(key)
        if group is None:
            raise TaskWorkError("找不到这组工作区。")
        if member_id is not None:
            for member in group["members"]:
                session = self._session(member["sid"])
                if session and member_id in (member["sid"], session.id):
                    return session.id
            raise TaskWorkError("这个仓库的任务不在这组工作区，或已删除。")
        if group["integration"] and (session := self._session(group["integration"])):
            return session.id
        missing = [item["repo"] for item in group["members"] if not Path(item["worktree"]).is_dir()]
        if missing:
            raise TaskWorkError("有仓库的任务工作区已经关闭，无法在这组分支上联调。")
        session = self.rt.create(str(self.rt.work_dir / f"group-{key}"), "ask")
        session.append({"type": "title", "title": f"{group['title']} · 联调"[:100]})
        session.append({"type": "task_group_brief", "text": self._brief(group, integration=True)})
        existing = group["integration"]
        group["integration"] = session.id
        try:
            self._save()
        except OSError:
            group["integration"] = existing
            self.rt.store.delete(session.id)
            raise
        return session.id

    @staticmethod
    def _brief(group: dict, *, integration: bool = False) -> str:
        parts = [f"多仓库任务：{group['title']}", f"接口约定：{group['contract']}", f"联调验收：{group['acceptance']}"]
        if integration:
            parts.append("在这组任务分支的工作区里做联调，记录每条检查的实际结果和失败输出。")
            parts.extend(f"- {item['repo']} → {item['worktree']}（{item['branch']}）" for item in group["members"])
        return "\n".join(parts)
