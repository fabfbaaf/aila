"""多智能体：用真的 git 仓库验证——派几个子 Agent 一起做、各在自己的分支里改，进度实时报出来；
收回时按改文件问、给你看 diff；同一个文件被改过就不收；放弃、删会话时拆干净；子 Agent 没法问就不做要问的事"""

import json
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from app.llm import Message, StepItem, ToolCall, Usage
from app.runtime.permissions import Policy, Rules
from app.runtime.runner import Runtime
from app.runtime.workers import REVIEW_PROMPT, WORKER_PROMPT, WorkerPolicy, open_workers

from .test_fix import make_repo, sh
from .test_runtime import SHELL, Scripted


def call(cid: str, name: str, **args: Any) -> ToolCall:
    return ToolCall(cid, name, json.dumps(args, ensure_ascii=False))


class Worker:
    """子 Agent 的假模型：task 里写「把 X 写成 Y」就写那个文件，再交报告；「跑 Z」就跑那条命令"""

    name = "worker"

    def __init__(self) -> None:
        self.requests: list[tuple[list[Message], list[dict[str, Any]] | None]] = []

    async def step(self, messages: list[Message], tools: list[dict[str, Any]] | None, max_tokens: int = 8192) -> AsyncIterator[StepItem]:
        self.requests.append((messages, tools))
        task = str(next(m["content"] for m in messages if m["role"] == "user")).rsplit("</", 1)[-1]
        task = str(messages[-1]["content"]) if len(self.requests) == 1 else task
        if len(self.requests) == 1:
            if m := re.search(r"把 (\S+) 写成 (\S+)", task):
                yield call("w1", "write_file", path=m.group(1), content=m.group(2) + "\n")
            elif m := re.search(r"跑 (.+)$", task):
                yield call("w1", "run_command", command=m.group(1))
            elif "看看改动" in task:
                yield call("w1", "run_command", command="git status")
            else:
                yield "没什么要改的。"
            yield Usage(prompt=100, completion=10, cache_hit=0, cache_miss=100)
            return
        yield "改好了，验证过。"
        yield Usage(prompt=120, completion=10, cache_hit=100, cache_miss=20)


def runtime(tmp_path: Path, main_steps: list[list[Any]]) -> tuple[Runtime, Scripted, list[Worker]]:
    main = Scripted(main_steps)
    workers: list[Worker] = []
    made = {"main": 0}

    def factory() -> Any:
        # 开会话时取一次（记模型名）、建主 Agent 时取一次，之后都是派出去的子 Agent
        if made["main"] < 2:
            made["main"] += 1
            return main
        w = Worker()
        workers.append(w)
        return w

    return Runtime(tmp_path / "data", factory, shell=SHELL), main, workers


async def run(rt: Runtime, sid: str, text: str, answers: list[bool] | None = None) -> list[dict[str, Any]]:
    """跑一轮，记下所有事件（包括只推给界面的进度）；遇到确认按 answers 回答"""
    answers = list(answers or [])
    out = []
    agent = rt.agent(sid)
    async for e in agent.turn(text):
        out.append(e)
        if e["type"] == "confirm":
            rt.confirms.answer(e["id"], answers.pop(0))
    return out


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path / "proj")


@pytest.mark.anyio
async def test_two_workers_in_parallel_then_merge(repo: Path, tmp_path: Path) -> None:
    rt, main, workers = runtime(tmp_path, [
        [call("d1", "delegate", title="写 a", task="把 a.txt 写成 AAA"), call("d2", "delegate", title="写 b", task="把 b.txt 写成 BBB")],
        [call("m1", "merge_work", id="1"), call("m2", "merge_work", id="2")],
        ["两块都收回来了。"],
    ])
    session = rt.create(str(repo), "auto")
    events = await run(rt, session.id, "加两个文件")
    assert events[-1]["reason"] == "done"

    # 两个子 Agent 一起跑，各在自己的分支里写；你的代码在收回之前不动
    tools = [e for e in events if e["type"] == "tool"]
    delegated = [e for e in tools if e["name"] == "delegate"]
    assert [e["status"] for e in delegated] == ["ok", "ok"]
    assert sorted(e["worker"] for e in delegated) == ["1", "2"]
    assert {e["files"][0] for e in delegated} == {"A a.txt", "A b.txt"}
    assert "merge_work" in delegated[0]["content"]
    # 子 Agent 用自己的系统提示词；看到的环境说明是它自己的临时目录
    messages, worker_tools = workers[0].requests[0]
    assert messages[0]["content"] == WORKER_PROMPT and "<分支>" in messages[1]["content"]
    assert {"edit_file", "write_file", "run_command", "web_search"} <= {t["function"]["name"] for t in worker_tools or []}
    assert "delegate" not in {t["function"]["name"] for t in worker_tools or []}  # 不能再往下派
    # 进度实时报给界面（不写进日志）
    progress = [e for e in events if e["type"] == "tool_progress"]
    assert any(e["who"] == "1 写 a" or e["who"] == "2 写 b" for e in progress) and any(e["text"] == "开分支" for e in progress) and all("seq" not in e for e in progress)
    assert not any(e["type"] == "tool_progress" for e in rt.agent(session.id).session.events)

    # 收回：文件进了你的代码（没提交），分支删掉
    merged = [e for e in tools if e["name"] == "merge_work"]
    assert [e["status"] for e in merged] == ["ok", "ok"]
    assert (repo / "a.txt").read_text(encoding="utf-8") == "AAA\n" and (repo / "b.txt").read_text(encoding="utf-8") == "BBB\n"
    assert "X = 2" in (repo / "notes.py").read_text(encoding="utf-8")  # 你没提交的改动还在
    assert open_workers(rt.agent(session.id).session.events) == {}
    assert "aila/task-" not in sh(repo, "branch", "--list")
    # 子 Agent 的用量记进主会话
    usage = [e for e in rt.agent(session.id).session.events if e["type"] == "usage" and e.get("purpose") == "delegate"]
    assert len(usage) == 2 and usage[0]["model"] == "worker"


@pytest.mark.anyio
async def test_merge_asks_with_diff_and_refuses_conflicts(repo: Path, tmp_path: Path) -> None:
    rt, main, _ = runtime(tmp_path, [
        [call("d1", "delegate", title="写 c", task="把 c.py 写成 X=3"),
         call("d2", "delegate", title="也写 c", task="把 c.py 写成 X=4")],
        [call("m1", "merge_work", id="1")],
        [call("m2", "merge_work", id="2")],
        [call("x2", "discard_work", id="2")],
        ["第二块和第一块冲突了，没收。"],
    ])
    session = rt.create(str(repo), "ask")
    events = await run(rt, session.id, "写 c", answers=[True, True])

    confirms = [e for e in events if e["type"] == "confirm"]
    assert confirms[0]["kind"] == "edit" and confirms[0]["tool"] == "merge_work"
    assert "c.py" in confirms[0]["detail"] and "+X=3" in confirms[0]["detail"]  # 给你看的是 git 算出来的 diff
    assert (repo / "c.py").read_text(encoding="utf-8") == "X=3\n"

    tools = {e["id"]: e for e in events if e["type"] == "tool"}
    assert tools["m2"]["status"] == "error" and tools["m2"]["conflicts"] == ["c.py"]
    assert (repo / "c.py").read_text(encoding="utf-8") == "X=3\n"  # 没被第二块覆盖
    assert tools["x2"]["status"] == "ok"
    assert open_workers(rt.agent(session.id).session.events) == {}
    assert "aila/task-" not in sh(repo, "branch", "--list")


@pytest.mark.anyio
async def test_read_only_cannot_delegate_work_but_can_review(repo: Path, tmp_path: Path) -> None:
    rt, _, workers = runtime(tmp_path, [
        [call("d1", "delegate", title="改", task="把 a.txt 写成 A")],
        [call("r1", "delegate", title="看改动", task="看看改动", role="review")],
        ["好。"],
    ])
    session = rt.create(str(repo), "read_only")
    events = await run(rt, session.id, "看看")
    tools = {e["id"]: e for e in events if e["type"] == "tool"}
    assert tools["d1"]["status"] == "denied" and not (repo / "a.txt").exists()
    assert tools["r1"]["status"] == "ok" and tools["r1"]["role"] == "review"
    messages, review_tools = workers[0].requests[0]
    assert messages[0]["content"] == REVIEW_PROMPT
    names = {t["function"]["name"] for t in review_tools or []}
    assert "run_command" in names and "edit_file" not in names and "write_file" not in names


def test_worker_policy_never_asks(tmp_path: Path) -> None:
    rules = Rules(tmp_path / "rules.json")
    project = tmp_path / "proj"
    ask = WorkerPolicy(project, rules, "ask")
    assert ask.decide("edit").verdict == "allow"  # 只改在自己的分支里
    assert ask.decide("command", "git status").verdict == "allow"
    assert ask.decide("command", "npm test").verdict == "deny"  # 先问再改：要问的命令它不跑
    rules.allow(project, "npm test")
    assert ask.decide("command", "npm test").verdict == "allow"  # 你以前允许过的可以
    auto = WorkerPolicy(project, rules, "auto")
    assert auto.decide("command", "pytest -q").verdict == "allow"
    assert auto.decide("command", "git push --force").verdict == "deny"  # 危险命令：没法问就不做
    assert auto.decide("command", "type .env").verdict == "deny"
    assert auto.decide("secret").verdict == "deny"
    assert auto.decide("mcp", "x").verdict == "deny"
    # 主会话：只读不让派，别的模式派的时候不用问
    assert Policy(project, rules, "read_only").decide("delegate").verdict == "deny"
    assert Policy(project, rules, "ask").decide("delegate").verdict == "allow"


@pytest.mark.anyio
async def test_worker_cannot_run_commands_needing_approval(repo: Path, tmp_path: Path) -> None:
    rt, _, workers = runtime(tmp_path, [
        [call("d1", "delegate", title="跑测试", task="跑 python -m pytest -q")],
        ["它跑不了测试，我来跑。"],
    ])
    session = rt.create(str(repo), "ask")
    events = await run(rt, session.id, "试试")
    tool = next(e for e in events if e["type"] == "tool")
    assert tool["status"] == "ok" and "没有改任何文件" in tool["content"]
    sub = [json for json in workers[0].requests[1][0] if json["role"] == "tool"]
    assert "要用户同意" in sub[0]["content"]  # 被挡下了，告诉它写进报告
    assert open_workers(rt.agent(session.id).session.events) == {}  # 没改东西：分支直接删了


@pytest.mark.anyio
async def test_delete_session_removes_open_worker_branches(repo: Path, tmp_path: Path) -> None:
    rt, _, _ = runtime(tmp_path, [[call("d1", "delegate", title="写 a", task="把 a.txt 写成 A")], ["先放着。"]])
    session = rt.create(str(repo), "auto")
    await run(rt, session.id, "写 a")
    opened = open_workers(rt.agent(session.id).session.events)
    assert list(opened) == ["1"] and Path(opened["1"].worktree).exists()
    await rt.delete(session.id)
    assert not Path(opened["1"].worktree).exists()
    assert "aila/task-" not in sh(repo, "branch", "--list")


@pytest.mark.anyio
async def test_delegate_outside_git(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    rt, _, _ = runtime(tmp_path, [[call("d1", "delegate", title="写 a", task="把 a.txt 写成 A")], ["那我自己做。"]])
    session = rt.create(str(plain), "auto")
    events = await run(rt, session.id, "写 a")
    tool = next(e for e in events if e["type"] == "tool")
    assert tool["status"] == "error" and "git" in tool["content"] and not (plain / "a.txt").exists()
