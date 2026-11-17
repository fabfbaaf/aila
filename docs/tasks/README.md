# 分出去的任务

这里每份说明都是自带上下文的：交给 Codex（或别的编程助手）时，让它先读仓库根目录的 `AGENTS.md`，再读对应的说明，照着做。

| 任务 | 做什么 | 主要改哪里 | 状态 |
|---|---|---|---|
| [01 后台命令](01-background-commands.md) | 开发服务器这类不会自己退出的命令：放后台跑、看输出、停掉 | `aila-ai/app/runtime/shell.py`、`tools.py` | 已合并（0079ab6） |
| [02 编程 Agent 评测](02-agent-eval.md) | 任务集、跑任务、自动判定做没做成、出报告（第 5 周要用） | 新的 `aila-ai/scripts/eval_agent.py`、`aila-ai/eval/` | 已合并（c656df3） |
| [03 管理记住的命令](03-permission-rules.md) | 「以后都允许」的命令：能看、能删（接口、设置页、终端） | `permissions.py`、`routes/agent.py`、新的设置分区 | 已合并（b965c6d） |
| [04 会话管理](04-session-management.md) | 会话改名、删除、搜索（接口、终端；任务页的按钮之后另做） | `session.py`、`runner.py`、`routes/agent.py`、终端版 | 已合并（b77dc8d） |
| [05 看管 AI 服务](05-service-keeper.md) | 桌面端自己启动 AI 服务、崩了重启、关程序时一起关 | 新的 `aila-desktop/src/main/service.ts`、设置页「运行状态」 | 已合并（6304d61） |
| [06 安装包](06-installer.md) | Windows 安装包，AI 服务打包在里面，不用装 Python、Node | `aila-ai/scripts/build_service.py`、`aila-desktop/electron-builder.yml` | 可以开始 |
| [07 撤销一轮](07-undo-turn.md) | 把她最近一轮改的文件退回去（用户之后改过的不碰） | 新的 `runtime/snapshots.py`、`tools.py`、`runner.py`、任务页、终端 | 可以开始 |
| [08 花费和上限](08-budget.md) | 按价格表算钱；给任务设上限，花到了就停 | 新的 `runtime/pricing.py`、`loop.py`、`session.py`、任务页、终端 | 等 07 |

## 先后顺序

改同一批文件的任务一个接一个做，不同的可以同时做。每个分支合并以后再开下一个依赖它的：

```
已合并：         01   02   03   04   05
现在可以同时开： 06   07
07 合并以后：    08
```

- 05、06 只改桌面端主进程和打包，和编程 Agent 的任务不冲突
- 04、07、08 都要改 `runner.py`、`routes/agent.py`、终端命令、任务页，所以排成一串

## 之后的（说明还没写）

前面的合并以后再写，因为要基于那时候的代码：

| 任务 | 做什么 | 要等什么 |
|---|---|---|
| 「等你」收件箱 | 所有会话里等你确认的事汇到一处：主窗口图标栏的角标、她头上的气泡（小改动能直接点「可以」）、终端 | 桔梗定：气泡里能不能直接同意 |
| 快速输入框 | 点她或 `Alt+Shift+A` 弹出一个输入框，按规则分流成聊天或编程任务；拖文件给她开任务 | 收件箱合并以后 |
| `/review` | 让她只读地审一遍没提交的改动，按严重程度列出问题 | 07 合并以后 |
| 项目记忆 | 她在任务里学到的项目约定记下来，下次开会话自动带上 | 08 合并以后 |
| 语音派任务 | 「你好艾拉，把刚才挂掉的测试修一下」直接开任务 | 快速输入框合并以后 |

## 规矩

- 每个任务一个分支：`codex/01-background-commands` 这样，从**最新的** `main` 开
- 几个任务都会碰的文件（`aila-ai/app/routes/agent.py`、`aila-ai/app/cli/commands.py`、`aila-desktop/src/main/index.ts`、`aila-desktop/src/preload/index.ts`）：只在末尾或者说明里指定的位置加自己的东西，别动别人的
- 桌面端现在是一个主窗口「艾拉」（`aila-desktop/src/renderer/src/shell/`，页面地址规则在 `src/shared/shell.ts`）：任务页是原来的工作窗口，设置、项目、资料、记忆都是它里面的页面。IPC 里原来「只认工作窗口」的地方现在只认主窗口（`main/index.ts` 的 `fromAgent`），新加的写操作照这个写
- 完成的标准：说明里的验收项都做到，`AGENTS.md` 里的测试全过；仓库里放一份 `docs/tasks/<编号>-<名字>-result.md`，写清改了什么、测试结果、没做完或者拿不准的地方（照 `01-background-commands-result.md` 的样子）
