# 07 撤销一轮

分支 `codex/07-undo-turn` 从 `main` 的 `b803637` 创建。

## 行为

- 终端输入 `/undo`，或在任务页最新一轮的结尾点「撤销这一轮」，先查看文件再确认。请求会带上确认时的轮次号。如果另一个客户端已经开始或完成了新的一轮，需要重新确认。
- 这一轮被文件工具多次修改的文件，按第一次修改前的字节恢复。没有再次被修改的新文件会删除。撤销前检查最后一次写入的指纹，同一轮中两次写入之间也检查文件有没有被别人改过。
- 跳过后来改过、移走、换成链接、缺少有效底片或可能存着密钥的文件，并逐项说明原因。在分支上做的会话只操作自己的临时工作目录。已经合并进项目的改动需要另行处理。
- 每轮只能撤销一次。结果作为 `undo` 事件追加到原日志，旧消息保持原样。下一条用户消息带上撤销说明，已恢复的文件在再次编辑前必须重新读取。
- 如果这一轮还执行过命令，结果会提醒命令做的事需要另行处理。底片保存在会话日志旁边，删除会话时一起清理。

## 改动文件

| 文件 | 内容 |
|---|---|
| `aila-ai/app/runtime/snapshots.py` | 备份原始字节，按文件归并本轮改动，检查路径、指纹和底片后恢复 |
| `aila-ai/app/runtime/tools.py`、`workspace.py`（同目录） | 读取并验证原始字节，写前留底，记录完成后的指纹，保护 Git 和 Windows 数据流路径 |
| `aila-ai/app/runtime/runner.py`、`session.py`（同目录） | 约束可撤销轮次和工作区，追加事件，给下一句用户消息添加说明，删除会话时清理底片 |
| `aila-ai/app/routes/agent.py` | `POST /agent/sessions/{sid}/undo`，校验可选的 `turn_seq` |
| `aila-ai/app/cli/app.py`、`backend.py`、`commands.py`（同目录） | 增加 `/undo`、双后端支持、文件预览和确认、结果和命令提示 |
| `aila-ai/tests/test_runtime_undo.py` | 运行时、Git worktree、API、终端两种后端的验收和回归 |
| `aila-desktop/src/shared/agent.ts`、`src/main/agent.ts`、`src/main/index.ts`、`src/preload/index.ts`（桌面端） | 共用结果类型、HTTP 转发、主窗口专用 IPC、预加载桥 |
| `aila-desktop/src/renderer/src/agent/fold.ts`、`AgentApp.vue`、`source.ts`（同目录） | 最新一轮的撤销入口，确认对话框，复用历史事件展示 |
| `aila-desktop/src/renderer/src/agent/UndoResult.vue`、`TraceView.vue`、`trace.ts`（同目录） | 过程和轨迹显示撤销结果，原轮次计时保持原样 |
| `aila-desktop/src/renderer/src/agent/agent.test.ts`、`src/main/agent.test.ts`、`src/preload/index.test.ts`（桌面端） | 验证入口出现时机、旧日志兼容、撤销记录回放、请求转发和过时响应 |
| `aila-ai/README.md`、`aila-desktop/README.md` | 撤销范围、使用方法和结果说明 |
| `docs/tasks/07-undo-turn-result.md` | 本说明 |

权限规则 `permissions.py`、已有日志事件及主窗口其他页面保持原样。

## 验证

| 命令 | 结果 |
|---|---|
| `.venv\Scripts\python -X utf8 -m pytest -q -p no:cacheprovider` | 408 通过，10 项按既有条件跳过 |
| 撤销与终端定向测试 | 25 通过 |
| `npm.cmd run typecheck` | 通过 |
| `npx.cmd vitest run` | 214 通过，1 项既有条件跳过 |
| `.\mvnw.cmd test` | 26 通过，0 失败，0 错误，0 跳过 |
| `npm.cmd run build` | 主进程、预加载及渲染层构建通过 |

Python 测试进程使用本机地址的 `NO_PROXY`，Java 测试使用本机 JDK 21，两者都仅设置当次进程环境。测试没有调用真实模型。定向自动化覆盖 CRLF、GBK、重复修改、用户另改、轮次去重、会话重载、压缩后说明、重读、真实 Git worktree、运行中拒绝、旧日志及命令副作用。

## 验证边界

- 自动检查在 Windows 上运行，桌面确认对话框已编译，入口时机和调用通过自动化验证。未在真实 Electron 窗口手点，也未在 Linux 或 macOS 上运行测试。
- 撤销范围是文件工具写入的文件内容。命令、已合并的原项目改动、文件之外的副作用和空目录由用户另行处理。旧会话事件缺少底片时会解释跳过原因。
