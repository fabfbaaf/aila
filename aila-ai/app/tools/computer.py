"""电脑操作技能包和多步电脑任务入口。命令参数由代码显示，执行前复核当前确认状态。"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..computer import computer_shell
from ..runtime.permissions import ComputerPolicy, Rules
from ..runtime.runner import Runtime, RuntimeError_
from ..runtime.shell import run as run_command
from . import APPROVED, Skill, Tool, emit

MAX_COMMAND = 6000
MAX_TASK = 20000


def _command(args: dict[str, Any]) -> str:
    value = args.get("command")
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_COMMAND or "\0" in value:
        raise ValueError("给一条有效的 PowerShell 命令，最多 6000 个字。")
    return value


def computer_skill() -> Skill:
    def policy() -> ComputerPolicy:
        from ..deps import ENV_FILE, settings

        # 对话每条写操作都单独确认，不继承电脑任务记住的命令。
        return ComputerPolicy(Path.home(), Rules(settings.data_dir / "computer_rules.json"), "ask",
                              data_dir=settings.data_dir, env_file=ENV_FILE, remember_commands=False)

    def validate(args: dict[str, Any]) -> str | None:
        try:
            _command(args)
        except ValueError as err:
            return str(err)
        return None

    def needs_confirmation(args: dict[str, Any]) -> bool:
        return policy().decide("command", _command(args)).verdict != "allow"

    async def execute(args: dict[str, Any]) -> str:
        command = _command(args)
        if needs_confirmation(args) and not APPROVED.get():
            return "这条电脑命令需要你确认，当前没有执行。"
        result = await run_command(computer_shell(), command, Path.home(), 120)
        return result.render(120)

    tool = Tool(
        "computer_command",
        "在这台电脑上用 PowerShell 做一两步小事，例如查看进程、打开记事本。"
        "只读命令直接执行，其他命令逐条确认。要完成多步目标时用 computer_task 先出方案。",
        {"type": "object", "properties": {"command": {"type": "string", "description": "完整的 PowerShell 命令"}},
         "required": ["command"]},
        execute, lambda _: "正在执行电脑命令", risk="mixed", needs_confirm=needs_confirmation,
        action="运行这条电脑命令", detail=lambda args: _command(args), validate=validate,
    )
    return Skill("computer", "电脑操作", "用 PowerShell 查看电脑或做一两步操作，每条修改都先确认",
                 [tool], default_enabled=False)


def computer_task_skill(runtime: Callable[[], Runtime]) -> Skill:
    def validate(args: dict[str, Any]) -> str | None:
        task = args.get("task")
        if not isinstance(task, str) or not task.strip() or len(task) > MAX_TASK:
            return "说清楚要在电脑上完成什么，最多 20000 个字。"
        return None

    async def start(args: dict[str, Any]) -> str:
        if not APPROVED.get():
            return "开电脑任务需要你确认，当前没有开始。"
        task = args["task"].strip()
        try:
            rt = runtime()
            session = rt.create(kind="computer", mode="ask")
            rt.start(session.id, task, plan=True)
        except RuntimeError_ as err:
            return f"电脑任务没开成：{err}"
        emit({"type": "agent_task", "session": session.id, "project": "电脑", "kind": "computer"})
        return "已在任务页开了电脑任务，先查看并提出分阶段方案。等用户批准方案后再做，修改前仍会逐项确认。"

    tool = Tool(
        "computer_task",
        "用户要在这台电脑上完成整理文件、批量改名、做短视频、查论文并保存简报等多步操作时用。"
        "先问用户开个电脑任务做，批准后在任务页出方案、按阶段推进。一两步小事可用 computer_command。",
        {"type": "object", "properties": {"task": {"type": "string", "description": "用户要完成的目标及必要细节"}},
         "required": ["task"]},
        start, lambda _: "正在开电脑任务", risk="write", action="开个电脑任务做",
        detail=lambda args: args["task"], validate=validate,
    )
    return Skill("computer_tasks", "电脑任务", "先确认目标，再在任务页出方案并分阶段操作电脑", [tool])
