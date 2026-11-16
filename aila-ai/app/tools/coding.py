"""「编程任务」技能包：你在对话里（打字或者说话）让她改代码、加功能、修 bug 时，她不在对话里写大段代码，
而是在工作窗口里开一个任务交给编程 Agent 去做——那里能读写你的项目、跑测试，每一步你都看得到、改之前会先问你。

项目按名字找（代码库里登记的、做过任务的）；对不上或者有好几个，就让她先问你是哪个。
开好以后推一个 agent_task 事件，桌面端据此打开工作窗口、接上这个任务。
"""

from collections.abc import Callable
from typing import Any

from ..runtime.runner import Runtime, RuntimeError_
from . import Skill, Tool, emit


def coding_skill(runtime: Callable[[], Runtime]) -> Skill:
    async def start(args: dict[str, Any]) -> str:
        task = str(args.get("task", "")).strip()
        name = str(args.get("project", "")).strip()
        if not task:
            return "要说清楚做什么。"
        rt = runtime()
        found = rt.find_project(name)
        if not found:
            return ("还不知道用户的项目在哪。请用户在主窗口的任务页点「新任务」选一个文件夹，或者在「设置 → 代码库」登记项目。"
                    if not name else f"没找到叫「{name}」的项目。问问用户是哪个文件夹。")
        if len(found) > 1:
            names = "、".join(n for n, _ in found[:8])
            return f"有好几个项目：{names}。问用户是哪一个，再调用一次（填 project）。"
        project, path = found[0]
        try:
            session = rt.create(path, "ask")
            rt.start(session.id, task)
        except RuntimeError_ as err:
            return f"没开成：{err}"
        emit({"type": "agent_task", "session": session.id, "project": project})
        return (f"已经在任务页里开始做了（项目：{project}）。改文件、跑命令之前会在任务页里问用户。"
                "简单告诉用户你去任务页里做了、做完会说一声；不要自己在对话里写代码。")

    tool = Tool(
        "coding_task",
        "用户要你在他的项目里写代码、改 bug、加功能、跑测试、重构这类编程任务时用：在任务页里开一个任务，"
        "由编程 Agent 去读代码、改文件、跑测试。不要自己在对话里写大段代码。只是问编程问题（不涉及他的项目）时不用它。",
        {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "要做的事：用用户的原话，补上对话里提到的必要信息（比如报错原文）"},
                "project": {"type": "string", "description": "项目名（用户提到了就填；只有一个项目时可以不填）"},
            },
            "required": ["task"],
        },
        start,
        lambda a: f"正在开编程任务{('（' + str(a.get('project')) + '）') if a.get('project') else ''}",
    )
    return Skill("coding", "编程任务", "让她在任务页里帮你改代码、加功能、修 bug、跑测试", [tool])
