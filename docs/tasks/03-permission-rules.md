# 03 管理「以后都允许」的命令

## 背景

编程 Agent 问用户要不要跑一条命令时，用户可以选「以后都允许」：这条命令（一字不差）在这个项目里以后不再问。规则存在 `aila-ai/data/agent_rules.json`，由 `aila-ai/app/runtime/permissions.py` 的 `Rules` 类读写（已有 `commands(project)`、`allow(project, command)`、`forget(project, command)`）。

现在用户没地方看、也没法撤销这些规则。

## 要做的

### AI 服务

- `Rules` 加 `all()`：返回 `{项目路径: [命令, ...]}`（只读，不改已有方法的行为）
- 两个接口，加在 `aila-ai/app/routes/agent.py` 文件末尾：
  - `GET /agent/rules` → `[{ "project": "...", "commands": ["npm test", ...] }]`，按项目路径排序
  - `DELETE /agent/rules`，请求体 `{ "project": "...", "command": "..." }` → 删掉这一条；没有这条返回 404
- 删掉以后马上生效（`Policy` 每次判断都现读规则，不用重启）

### 桌面端：设置窗口里加一页「编程」

- 新文件 `aila-desktop/src/renderer/src/settings/sections/AgentSection.vue`：
  - 上面一段说明三种权限模式（文字可以照 `aila-desktop/src/shared/agent.ts` 的 `MODE_LABELS`、`MODE_HINTS`），以及「危险命令不管哪种模式都要问，也不能以后都允许」
  - 下面按项目列出记住的命令，每条一个「删掉」按钮；没有时显示一句空状态说明
- 接上：`src/shared/settings.ts` 的 `SettingsSection` 加 `'agent'`；`SettingsApp.vue` 的导航加一项「编程」；`src/main/ai.ts` 加两个请求函数；`src/main/index.ts` 加两个 IPC（`agent:rules`、`agent:rules:forget`，只接受设置窗口发来的，照 `agent:*` 那几个的写法检查发送方和参数）；`src/preload/index.ts` 加两个方法
- 样式照其他设置页（比如 `CodeSection.vue`）

### 终端版

- `aila-ai/app/cli/commands.py` 的 `COMMANDS` 加 `/rules`；`app.py` 的 `Terminal.command` 加对应分支：
  - `/rules`：列出当前项目记住的命令（带编号）
  - `/rules forget 编号`：删掉一条
- 两种后端（`EmbeddedBackend`、`HttpBackend`，在 `app/cli/backend.py`）各加 `rules()`、`forget_rule()`；只加方法，不改已有的

## 不要改

- 权限判断的逻辑（`Policy.decide`）
- 工作窗口（`src/renderer/src/agent/`）

## 验收

- `aila-ai/tests/test_permission_rules.py`：`all()`；两个接口（列出、删掉、删不存在的 404）；删掉以后同一条命令又要问（用 `Policy.decide` 验证）
- 终端：`/rules`、`/rules forget 1` 的测试（参考 `tests/test_cli.py` 里直接调 `term.command(...)` 的写法）
- 桌面端 `npm.cmd run typecheck` 通过；在浏览器调试模式下（`npm.cmd run dev:web`，打开 `http://127.0.0.1:5180/#/settings/agent`）这一页能显示、能删
- `AGENTS.md` 里的测试全过
