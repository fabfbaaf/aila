"""后台命令用真实子进程验证：输出、权限和生命周期都不能只靠假对象推断。"""

import asyncio
import ctypes
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from app.runtime import shell as shell_runtime
from app.runtime.runner import Runtime
from app.runtime.shell import find_shell
from app.runtime.tools import CodingTools, Outcome
from app.runtime.workspace import Workspace
from tests.test_runtime import Scripted, call, drive, make_agent

SHELL = find_shell()


def script(root: Path, body: str, name: str = "job.py") -> str:
    path = root / name
    path.write_text(body, encoding="utf-8")
    prefix = "& " if SHELL.kind == "powershell" else ""
    return f'{prefix}"{Path(sys.executable).as_posix()}" "{path.as_posix()}"'


def gated_script(root: Path, body: str, name: str = "job.py") -> str:
    return script(root, "import os, sys, time\nfrom pathlib import Path\n"
                  "os.write(1, b'ready\\n')\n"
                  "while not Path('release').exists():\n    time.sleep(0.01)\n" + body, name)


async def until(check: Callable[[], bool], timeout: float = 5) -> None:
    async with asyncio.timeout(timeout):
        while not check():
            await asyncio.sleep(0.01)


def process_running(pid: int) -> bool:
    """只查询这次测试创建的进程，Windows 不依赖 tasklist 或 psutil。"""
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        status = Path(f"/proc/{pid}/stat")
        if status.exists():
            # Linux 的孤儿进程可能还在等 init 收尸；僵尸已经不会执行代码。
            try:
                return status.read_text().rsplit(")", 1)[-1].split()[0] != "Z"
            except FileNotFoundError:
                return False
        return True
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    kernel.GetExitCodeProcess.restype = ctypes.c_int
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_uint32()
        assert kernel.GetExitCodeProcess(handle, ctypes.byref(code))
        return code.value == 259  # STILL_ACTIVE
    finally:
        kernel.CloseHandle(handle)


async def start(tools: CodingTools, command: str) -> Outcome:
    result = await tools.tools["run_command"].run({"command": command, "background": True})
    assert result.ok, result.content
    assert result.extra["job_id"] in result.content
    return result


@pytest.mark.anyio
async def test_background_returns_ready_without_waiting_and_uses_clean_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in ("LLM_API_KEY", "SEARCH_API_KEY", "AILA_ACCOUNT_TOKEN"):
        monkeypatch.setenv(key, "test-secret-value")
    monkeypatch.setenv("AILA_JOB_TEST", "ordinary-value")
    command = script(tmp_path, "import os, sys, time\n"
                     "print(os.getcwd())\nprint(os.getenv('AILA_JOB_TEST'))\n"
                     "print([os.getenv(k) for k in ('LLM_API_KEY', 'SEARCH_API_KEY', 'AILA_ACCOUNT_TOKEN')])\n"
                     "print('ready', flush=True)\ntime.sleep(60)\n")
    tools = CodingTools(Workspace(tmp_path), SHELL)
    try:
        t0 = time.perf_counter()
        result = await asyncio.wait_for(start(tools, command), timeout=10)
        assert time.perf_counter() - t0 < 10  # 最多等启动窗口（3 秒）就回来；机器很忙时留点余量
        assert result.extra["job_id"] == "job-1" and "还在跑" in result.content
        # 机器很忙时子进程启动可能超过启动窗口：那就接着看输出，直到它打印 ready
        output = result.content
        deadline = time.perf_counter() + 20
        while "ready" not in output and time.perf_counter() < deadline:
            await asyncio.sleep(0.2)
            output += (await tools.tools["job_output"].run({"id": "job-1"})).content
        assert "ready" in output
        assert str(tmp_path) in output and "ordinary-value" in output
        assert "test-secret-value" not in output and "[None, None, None]" in output
        assert tools.jobs.jobs["job-1"].proc.returncode is None
    finally:
        await tools.jobs.shutdown()


@pytest.mark.anyio
async def test_silent_background_returns_after_startup_window(tmp_path: Path) -> None:
    tools = CodingTools(Workspace(tmp_path), SHELL)
    try:
        t0 = time.perf_counter()
        result = await asyncio.wait_for(start(tools, script(tmp_path, "import time\ntime.sleep(60)\n")), 5)
        assert 2.5 <= time.perf_counter() - t0 < 5
        assert "还在跑" in result.content
    finally:
        await tools.jobs.shutdown()


@pytest.mark.anyio
async def test_job_output_has_only_new_tail_and_reports_exit_code(tmp_path: Path) -> None:
    tools = CodingTools(Workspace(tmp_path), SHELL)
    command = gated_script(tmp_path, "for i in range(50):\n    os.write(1, ('later-' + str(i) + '\\n').encode())\nsys.exit(7)\n")
    try:
        result = await start(tools, command)
        jid = result.extra["job_id"]
        (tmp_path / "release").touch()
        # 后台任务的生命周期包含子树，shell 退出通知可能早于 Windows 的整树退出记账。
        await asyncio.wait_for(asyncio.shield(tools.jobs.jobs[jid].task), 5)
        output = await tools.tools["job_output"].run({"id": jid, "tail": 2})
        assert output.ok and "已退出" in output.content and "退出码 7" in output.content
        assert output.extra["exit_code"] == 7
        assert "later-48" in output.content and "later-49" in output.content
        assert "later-47" not in output.content and "ready" not in output.content
        assert output.extra["total_bytes"] == len(("ready\n" + "".join(f"later-{i}\n" for i in range(50))).encode())
        again = await tools.tools["job_output"].run({"id": jid})
        assert "later-" not in again.content and "已退出" in again.content
        assert again.extra["total_bytes"] == output.extra["total_bytes"]
    finally:
        await tools.jobs.shutdown()


@pytest.mark.anyio
async def test_default_output_tail_is_40_lines(tmp_path: Path) -> None:
    tools = CodingTools(Workspace(tmp_path), SHELL)
    try:
        result = await start(tools, gated_script(tmp_path, "for i in range(50):\n    print('line-' + str(i))\n"))
        jid = result.extra["job_id"]
        (tmp_path / "release").touch()
        await asyncio.wait_for(tools.jobs.jobs[jid].proc.wait(), 5)
        output = await tools.tools["job_output"].run({"id": jid})
        assert "line-10" in output.content and "line-49" in output.content
        assert "line-9\n" not in output.content
    finally:
        await tools.jobs.shutdown()


@pytest.mark.anyio
async def test_job_output_decodes_gbk_and_strips_ansi(tmp_path: Path) -> None:
    tools = CodingTools(Workspace(tmp_path), SHELL)
    payload = b"\x1b[31m" + "后来输出：找不到文件".encode("gbk") + b"\x1b[0m\n"
    try:
        result = await start(tools, gated_script(tmp_path, f"os.write(1, {payload!r})\n"))
        jid = result.extra["job_id"]
        (tmp_path / "release").touch()
        await asyncio.wait_for(tools.jobs.jobs[jid].proc.wait(), 5)
        output = await tools.tools["job_output"].run({"id": jid})
        assert "后来输出：找不到文件" in output.content
        assert "\x1b" not in output.content
        assert output.extra["total_bytes"] == len(b"ready\n") + len(payload)
    finally:
        await tools.jobs.shutdown()


@pytest.mark.anyio
@pytest.mark.parametrize("encoding, text, split", [("utf-8", "你", 2), ("gbk", "中", 1)])
async def test_multibyte_character_split_across_output_reads(
    tmp_path: Path, encoding: str, text: str, split: int,
) -> None:
    tools = CodingTools(Workspace(tmp_path), SHELL)
    encoded = text.encode(encoding)
    command = gated_script(tmp_path, f"os.write(1, b'first-' + {encoded[:split]!r})\n"
                           "Path('partial').touch()\n"
                           "while not Path('finish').exists():\n    time.sleep(0.01)\n"
                           f"os.write(1, {encoded[split:]!r} + b'-last\\n')\n")
    try:
        result = await start(tools, command)
        jid = result.extra["job_id"]
        (tmp_path / "release").touch()
        await until(lambda: (tmp_path / "partial").exists())
        await until(lambda: tools.jobs.jobs[jid].buffer.total_bytes == len(b"ready\nfirst-") + split)
        first = await tools.tools["job_output"].run({"id": jid})
        assert "first-" in first.content
        assert first.extra["total_bytes"] == len(b"ready\nfirst-") + split
        (tmp_path / "finish").touch()
        await asyncio.wait_for(tools.jobs.jobs[jid].proc.wait(), 5)
        second = await tools.tools["job_output"].run({"id": jid})
        assert text + "-last" in second.content
        assert "first-" not in second.content and "\ufffd" not in first.content + second.content
        assert second.extra["total_bytes"] == len(b"ready\nfirst--last\n") + len(encoded)
    finally:
        await tools.jobs.shutdown()


@pytest.mark.anyio
async def test_output_flood_keeps_bounded_head_and_tail_and_byte_count(tmp_path: Path) -> None:
    tools = CodingTools(Workspace(tmp_path), SHELL)
    try:
        command = gated_script(tmp_path, "os.write(1, b'H' * 4000 + b'M' * 200000 + b'T' * 20000)\n")
        result = await start(tools, command)
        jid = result.extra["job_id"]
        (tmp_path / "release").touch()
        await asyncio.wait_for(tools.jobs.jobs[jid].proc.wait(), 5)
        output = await tools.tools["job_output"].run({"id": jid, "tail": 100000})
        assert len(output.content) < 25000
        assert "H" * 1000 in output.content and "T" * 1000 in output.content
        assert output.content.count("M") < 1000 and "省略" in output.content
        assert output.extra["total_bytes"] == 224006
    finally:
        await tools.jobs.shutdown()


@pytest.mark.anyio
async def test_job_stop_kills_process_and_returns_final_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tools = CodingTools(Workspace(tmp_path), SHELL)
    try:
        result = await start(tools, script(tmp_path, "import time\nprint('ready', flush=True)\ntime.sleep(60)\n"))
        jid = result.extra["job_id"]
        proc = tools.jobs.jobs[jid].proc
        stopped = await asyncio.wait_for(tools.tools["job_stop"].run({"id": jid}), 10)
        assert stopped.ok and proc.returncode is not None
        assert "已退出" in stopped.content and "ready" in stopped.content
        repeated_kills: list[int] = []
        monkeypatch.setattr(shell_runtime, "kill_tree", lambda pid: repeated_kills.append(pid))
        repeated = await tools.tools["job_stop"].run({"id": jid})
        assert repeated.ok and "已退出" in repeated.content
        assert not repeated_kills  # 陈旧 PID 可能被复用，重复停止不能误杀其他进程。
    finally:
        await tools.jobs.shutdown()


@pytest.mark.anyio
async def test_job_stop_kills_descendant_process_too(tmp_path: Path) -> None:
    script(tmp_path, "import os, time\nfrom pathlib import Path\n"
           "Path('child.pid').write_text(str(os.getpid()))\ntime.sleep(60)\n", "child.py")
    command = script(tmp_path, "import subprocess, sys, time\nfrom pathlib import Path\n"
                     "subprocess.Popen([sys.executable, str(Path('child.py').resolve())])\n"
                     "while not Path('child.pid').exists():\n    time.sleep(0.01)\n"
                     "print('ready', flush=True)\ntime.sleep(60)\n")
    tools = CodingTools(Workspace(tmp_path), SHELL)
    try:
        result = await start(tools, command)
        jid = result.extra["job_id"]
        child_pid = int((tmp_path / "child.pid").read_text())
        assert process_running(child_pid)
        stopped = await asyncio.wait_for(tools.tools["job_stop"].run({"id": jid}), 10)
        assert stopped.ok and tools.jobs.jobs[jid].proc.returncode is not None
        await until(lambda: not process_running(child_pid))
    finally:
        await tools.jobs.shutdown()


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["job_stop", "shutdown"])
@pytest.mark.parametrize("redirect_output", [False, True], ids=["inherited-output", "detached-output"])
async def test_children_remain_managed_after_parent_exits(
    tmp_path: Path, operation: str, redirect_output: bool,
) -> None:
    script(tmp_path, "import os, time\nfrom pathlib import Path\n"
           "Path('child.pid').write_text(str(os.getpid()))\ntime.sleep(60)\n", "child.py")
    redirection = ", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL" if redirect_output else ""
    command = script(tmp_path, "import subprocess, sys, time\nfrom pathlib import Path\n"
                     f"subprocess.Popen([sys.executable, str(Path('child.py').resolve())]{redirection})\n"
                     "while not Path('child.pid').exists():\n    time.sleep(0.01)\n"
                     "print('ready', flush=True)\n", "parent.py")
    normal_command = script(tmp_path, "import time\nprint('ready', flush=True)\ntime.sleep(60)\n", "server.py")
    rt = Runtime(tmp_path / "data", lambda: Scripted([["好"]]), shell=SHELL)
    tools = rt.agent(rt.create(str(tmp_path), "auto").id).tools
    try:
        result = await start(tools, command)
        jid = result.extra["job_id"]
        job = tools.jobs.jobs[jid]
        # 父进程的退出码已经是 0，但它开的服务器仍属于同一个后台任务。
        await until(lambda: job.proc.returncode == 0)
        child_pid = int((tmp_path / "child.pid").read_text())
        assert process_running(child_pid)
        output = await tools.tools["job_output"].run({"id": jid})
        assert output.ok and "还在跑" in output.content
        for _ in range(2):
            await start(tools, normal_command)
        denied = await tools.tools["run_command"].run({"command": normal_command, "background": True})
        assert not denied.ok and "3" in denied.content
        if operation == "job_stop":
            stopped = await asyncio.wait_for(tools.tools["job_stop"].run({"id": jid}), 10)
            assert stopped.ok and "已退出" in stopped.content
            assert all(other.proc.returncode is None for id, other in tools.jobs.jobs.items() if id != jid)
        else:
            await asyncio.wait_for(rt.shutdown(), 15)
            assert all(other.proc.returncode is not None for other in tools.jobs.jobs.values())
        await until(lambda: not process_running(child_pid))
    finally:
        await rt.shutdown()


@pytest.mark.anyio
async def test_fourth_job_is_rejected_and_stopping_one_releases_slot(tmp_path: Path) -> None:
    tools = CodingTools(Workspace(tmp_path), SHELL)
    command = script(tmp_path, "import time\nprint('ready', flush=True)\ntime.sleep(60)\n")
    try:
        results = [await start(tools, command) for _ in range(3)]
        assert [r.extra["job_id"] for r in results] == ["job-1", "job-2", "job-3"]
        denied = await tools.tools["run_command"].run({"command": command, "background": True})
        assert not denied.ok and "3" in denied.content and "停" in denied.content
        await tools.tools["job_stop"].run({"id": "job-2"})
        replacement = await start(tools, command)
        assert replacement.extra["job_id"] == "job-4"
    finally:
        await tools.jobs.shutdown()


@pytest.mark.anyio
async def test_simultaneous_starts_cannot_exceed_three_jobs(tmp_path: Path) -> None:
    tools = CodingTools(Workspace(tmp_path), SHELL)
    command = script(tmp_path, "import time\nprint('ready', flush=True)\ntime.sleep(60)\n")
    try:
        results = await asyncio.gather(*(
            tools.tools["run_command"].run({"command": command, "background": True}) for _ in range(4)
        ))
        assert sum(result.ok for result in results) == 3
        assert len(tools.jobs.jobs) == 3
        assert len({result.extra["job_id"] for result in results if result.ok}) == 3
    finally:
        await tools.jobs.shutdown()


@pytest.mark.anyio
async def test_exited_jobs_do_not_consume_running_slots(tmp_path: Path) -> None:
    tools = CodingTools(Workspace(tmp_path), SHELL)
    command = script(tmp_path, "print('ready', flush=True)\n")
    try:
        for i in range(1, 5):
            result = await start(tools, command)
            assert result.extra["job_id"] == f"job-{i}"
            await asyncio.wait_for(asyncio.shield(tools.jobs.jobs[f"job-{i}"].task), 5)
    finally:
        await tools.jobs.shutdown()


@pytest.mark.anyio
async def test_unknown_job_is_a_tool_error_and_tool_order_stays_stable(tmp_path: Path) -> None:
    tools = CodingTools(Workspace(tmp_path), SHELL)
    assert list(tools.tools)[-3:] == ["todo_write", "job_output", "job_stop"]
    assert tools.tools["run_command"].kind({"background": True}) == "command"
    assert tools.tools["job_output"].kind({}) == "read"
    assert tools.tools["job_stop"].kind({}) == "meta"
    for name in ("job_output", "job_stop"):
        result = await tools.tools[name].run({"id": "job-999"})
        assert not result.ok and "job-999" in result.content


@pytest.mark.anyio
async def test_runtime_shutdown_kills_jobs_from_all_sessions(tmp_path: Path) -> None:
    model = Scripted([["好"]])
    rt = Runtime(tmp_path / "data", lambda: model, shell=SHELL)
    command = script(tmp_path, "import time\nprint('ready', flush=True)\ntime.sleep(60)\n")
    processes = []
    try:
        for _ in range(2):
            sid = rt.create(str(tmp_path), "auto").id
            tools = rt.agent(sid).tools
            result = await start(tools, command)
            assert result.extra["job_id"] == "job-1"  # 任务号和限额属于各自的会话
            processes.append(tools.jobs.jobs["job-1"].proc)
        await asyncio.wait_for(rt.shutdown(), 15)
        assert all(proc.returncode is not None for proc in processes)
    finally:
        await rt.shutdown()


@pytest.mark.anyio
async def test_job_survives_turn_end_and_is_available_next_turn(tmp_path: Path) -> None:
    command = script(tmp_path, "import time\nprint('ready', flush=True)\ntime.sleep(60)\n")
    model = Scripted([[call("run_command", command=command, background=True)], ["开着了"]])
    rt = Runtime(tmp_path / "data", lambda: model, shell=SHELL)
    sid = rt.create(str(tmp_path), "auto").id
    try:
        first = [e async for e in rt.run(sid, "启动")]
        assert first[-1]["reason"] == "done"
        proc = rt.agent(sid).tools.jobs.jobs["job-1"].proc
        assert proc.returncode is None
        model.steps = [[call("job_output", id="job-1"), call("job_stop", id="job-1")]]
        model.requests.clear()
        model.steps.append(["停好了"])
        second = [e async for e in rt.run(sid, "看日志再停掉")]
        results = [e for e in second if e["type"] == "tool"]
        assert len(results) == 2 and all(e["status"] == "ok" for e in results)
        assert "还在跑" in results[0]["content"] and "已退出" in results[1]["content"]
        assert not any(e["type"] == "confirm" for e in second)
        assert proc.returncode is not None
    finally:
        await rt.shutdown()


@pytest.mark.anyio
async def test_interrupt_during_background_startup_keeps_job_alive(tmp_path: Path) -> None:
    command = script(tmp_path, "import time\nfrom pathlib import Path\nPath('launched').touch()\ntime.sleep(60)\n")
    model = Scripted([[call("run_command", command=command, background=True)], ["开着了"]])
    rt = Runtime(tmp_path / "data", lambda: model, shell=SHELL)
    sid = rt.create(str(tmp_path), "auto").id
    try:
        rt.start(sid, "启动")
        await until(lambda: (tmp_path / "launched").exists())
        assert rt.interrupt(sid)
        await until(lambda: not rt.running(sid))
        proc = rt.agent(sid).tools.jobs.jobs["job-1"].proc
        assert proc.returncode is None
        output = await rt.agent(sid).tools.tools["job_output"].run({"id": "job-1"})
        assert output.ok and "还在跑" in output.content
        assert rt.agent(sid).session.events[-1]["reason"] == "interrupted"
    finally:
        await rt.shutdown()
    assert proc.returncode is not None


@pytest.mark.anyio
@pytest.mark.parametrize("mode, command", [("ask", "npm run dev"), ("auto", "rm -rf x &")])
async def test_background_commands_still_require_confirmation(tmp_path: Path, mode: str, command: str) -> None:
    model = Scripted([[call("run_command", command=command, background=True)], ["不会执行"]])
    agent = make_agent(tmp_path, tmp_path / "agent", model, mode=mode)
    try:
        events = await drive(agent, "执行", answers=[(False, False)])
        confirms = [e for e in events if e["type"] == "confirm"]
        assert len(confirms) == 1 and confirms[0]["kind"] == "command"
        assert command in confirms[0]["detail"]
        assert confirms[0]["can_remember"] == (mode == "ask")
        assert events[-1]["reason"] == "rejected" and not agent.tools.jobs.jobs
    finally:
        await agent.tools.jobs.shutdown()


@pytest.mark.anyio
async def test_confirming_background_command_launches_it(tmp_path: Path) -> None:
    command = script(tmp_path, "import time\nprint('ready', flush=True)\ntime.sleep(60)\n")
    model = Scripted([[call("run_command", command=command, background=True)], ["开着了"]])
    agent = make_agent(tmp_path, tmp_path / "agent", model)
    try:
        events = await drive(agent, "启动", answers=[(True, False)])
        assert len([e for e in events if e["type"] == "confirm"]) == 1
        result = next(e for e in events if e["type"] == "tool")
        assert result["status"] == "ok" and "job-1" in result["content"]
        assert agent.tools.jobs.jobs["job-1"].proc.returncode is None
    finally:
        await agent.tools.jobs.shutdown()


@pytest.mark.anyio
async def test_read_only_mode_denies_background_command(tmp_path: Path) -> None:
    model = Scripted([[call("run_command", command="npm run dev", background=True)], ["只看代码"]])
    agent = make_agent(tmp_path, tmp_path / "agent", model, mode="read_only")
    try:
        events = await drive(agent, "启动")
        results = [e for e in events if e["type"] == "tool"]
        assert len(results) == 1 and results[0]["status"] == "denied"
        assert "只读" in results[0]["content"] and not agent.tools.jobs.jobs
        assert not any(e["type"] == "confirm" for e in events)
    finally:
        await agent.tools.jobs.shutdown()
