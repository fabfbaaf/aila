# 04 会话管理：改名、删除、搜索

## 背景

编程 Agent 的会话存在 `aila-ai/data/sessions/<id>.jsonl`，是只追加的事件日志（`aila-ai/app/runtime/session.py`）。会话标题现在是第一句话的第一行（`Session.title`）。会话越来越多，用户需要改名、删掉不要的、按关键词找。

## 要做的

### AI 服务

- 改名：会话日志里追加一条 `{"type": "title", "title": "..."}` 事件（不改旧事件，日志只追加）；`Session.title` 有这种事件时用最新的那条，没有时照旧。`title` 事件不进发给模型的消息（`derive_messages` 只认 user / assistant / tool，不用动它）
- 删除：`Runtime.delete(sid)`
  - 正在做的会话不能删（报错，说先打断）
  - 在新分支上做的会话（`session.branch` 不为空）：先放弃分支（`runtime/branch.py` 的 `discard`），再删
  - 删日志文件，并从 `SessionStore` 和 `Runtime` 的内存里移除
- 搜索：`SessionStore.list` 加可选参数 `query`（标题或项目路径里包含，不分大小写）和 `cwd`（只要这个项目目录的，路径比较不分大小写和斜杠方向）
- 接口（`aila-ai/app/routes/agent.py`，加在文件末尾）：
  - `PATCH /agent/sessions/{id}`，`{ "title": "..." }`（1–100 字）
  - `DELETE /agent/sessions/{id}`：正在做 → 409
  - `GET /agent/sessions` 支持 `?q=关键词&cwd=项目目录`

### 终端版

- `aila-ai/app/cli/commands.py` 和 `app.py`：
  - `/rename 新标题`
  - `/delete`：问一句（y/n），删掉当前会话，然后在同一个目录开一个新会话
  - `/resume 关键词`：已有的 `/resume` 支持按关键词筛（编号、会话 id 的用法不变）
- 两种后端（`app/cli/backend.py`）各加 `rename()`、`delete()`；只加方法

### 桌面端（只接到预加载，界面之后另做）

- `aila-desktop/src/main/agent.ts` 的 `AgentRelay` 加 `rename()`、`delete()`
- `aila-desktop/src/main/index.ts` 加 `agent:rename`、`agent:delete` 两个 IPC（只认工作窗口，照已有的 `agent:*` 检查）
- `aila-desktop/src/preload/index.ts` 加 `agentRename()`、`agentDelete()`
- 工作窗口的界面（`src/renderer/src/agent/`）**不要改**，另一个任务正在改这些文件

## 验收

- `aila-ai/tests/test_session_management.py`：
  - 改名后标题变了，重新加载（新的 `SessionStore`）还是新标题；发给模型的消息不受影响
  - 删除：文件没了、列表里没了；正在做的删不了；在新分支上做的会话删除时分支和临时目录也清掉（参考 `tests/test_agent_branch.py` 用真 git 仓库的写法）
  - 搜索：按标题、按项目目录
  - 三个接口
- 终端：`/rename`、`/delete`、`/resume 关键词` 的测试
- 桌面端 `npm.cmd run typecheck` 通过
- `AGENTS.md` 里的测试全过
