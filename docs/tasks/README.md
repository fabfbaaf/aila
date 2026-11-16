# 分出去的任务

这里每份说明都是自带上下文的：交给 Codex（或别的编程助手）时，让它先读仓库根目录的 `AGENTS.md`，再读对应的说明，照着做。

| 任务 | 做什么 | 主要改哪里 |
|---|---|---|
| [01 后台命令](01-background-commands.md) | 开发服务器这类不会自己退出的命令：放后台跑、看输出、停掉 | `aila-ai/app/runtime/shell.py`、`tools.py` |
| [02 编程 Agent 评测](02-agent-eval.md) | 任务集、跑任务、自动判定做没做成、出报告（第 5 周要用） | 新的 `aila-ai/scripts/eval_agent.py`、`aila-ai/eval/` |
| [03 管理记住的命令](03-permission-rules.md) | 「以后都允许」的命令：能看、能删（接口、设置窗口、终端） | `permissions.py`、`routes/agent.py`、新的设置页 |
| [04 会话管理](04-session-management.md) | 会话改名、删除、搜索（接口、终端；工作窗口的按钮之后另做） | `session.py`、`runner.py`、`routes/agent.py`、终端版 |

## 规矩

- 每个任务一个分支：`codex/01-background-commands` 这样，从 `main` 开
- 四个任务改的文件尽量不重叠；`aila-ai/app/routes/agent.py`、`aila-ai/app/cli/app.py`、`aila-ai/app/cli/commands.py` 几个任务都会碰，只在文件末尾或者对应的分支里加自己的东西，别动别人的
- 同时在做的还有「执行轨迹」和「上下文账本」（工作窗口 `aila-desktop/src/renderer/src/agent/`、终端的 `aila-ai/app/cli/render.py`），这些文件除非说明里写了，不要改
- 完成的标准：说明里的验收项都做到，`AGENTS.md` 里的测试全过；分支说明写清改了什么、测试结果、没做完或者拿不准的地方
