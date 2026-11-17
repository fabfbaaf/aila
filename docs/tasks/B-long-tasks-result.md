# B 长任务和整体规划：完成说明

分支：`claude/long-tasks`（从合并了 A 的 `main` 开）。Claude 做的，没有交给 Codex。

## 做了什么

- **出方案**：终端 `/plan 目标`、任务页输入框下的「出方案」、新任务里勾「先出方案再动手」。这一轮只读（复用 /review 的只读把关），请求由代码生成；她用新工具 `propose_plan` 交 2～8 个阶段（每阶段目标、怎么验收），交完就停
- **你决定**：任务页的计划卡片「照这个做」「不用了」，可以勾「每个阶段做完停一下」；终端 `/plan go`（`/plan go 每段停`）、`/plan no`；想改计划直接说，她重新交一份（新的代替旧的）
- **按阶段推进**：`Runtime.drive_plan` 一阶段一阶段地开新的一轮，开始的那句话由代码生成；她做完调用 `stage_done`，卡住调用 `stage_blocked`。一轮走满步数没做完就自动接着做，最多 5 次
- **停下**：打断、花到上限、出错、你没同意某个操作、等确认超时、她说卡住、接着做了 5 次还没完、你要求每阶段停一下——记一条 `plan_pause`（带原因），全部做完记 `plan_end`。停下以后「接着做」（任务页右边、`/plan continue`）从停的地方继续；服务重启以后也一样，不会自己接着跑
- **状态只从日志推**：`planning.plan_state`（Python）和 `agent/plan.ts`（桌面端）同一套规则；新事件 `plan_decision`、`plan_pause`、`plan_end` 不进模型消息；阶段指令只追加在历史末尾，前缀缓存不受影响
- **界面**：计划卡片、每阶段一行「▶ 第 N/M 阶段：……」、右边一栏计划进度和「接着做第 N 阶段」、决定 / 停下 / 做完各一行说明
- **桌面上的她**：按计划做时每轮不说「做完了」；一个阶段做完、整个计划做完、计划停下各说一句
- **假模型**：没填密钥也能把长任务演示一遍（交两个阶段，每阶段读 README 就标完成）
- **接口**：`messages` 带 `plan: true`；`/plan/approve {pause_each}`、`/plan/continue`（返回一直到停下的事件流）、`/plan/reject`；会话列表多一个 `plan` 进度字段

## 改了哪些文件

| 文件 | 改动 |
|---|---|
| `aila-ai/app/runtime/planning.py`（新） | 计划状态、三个工具、阶段指令 |
| `aila-ai/app/runtime/runner.py` | 出方案、决定、按阶段推进的循环、停下的原因；`_drive` 可以不关订阅 |
| `aila-ai/app/runtime/session.py` | 标题用代码给的 `title`；`summary` 带计划进度；事件说明 |
| `aila-ai/app/runtime/mock.py` | 假模型会出方案、按阶段做 |
| `aila-ai/app/routes/agent.py`、`app/cli/*` | 接口；`/plan` 命令和两种后端 |
| `aila-desktop/src/renderer/src/agent/plan.ts`、`PlanCard.vue`（新） | 计划状态、计划卡片 |
| `aila-desktop/src/renderer/src/agent/AgentApp.vue`、`fold.ts`、`source.ts` | 出方案、右边计划进度、阶段行、说明行 |
| `aila-desktop/src/main/agent.ts`、`index.ts`、`preload/index.ts`、`shared/agent.ts` | 转发、IPC（只认主窗口）、进展 |
| `aila-desktop/src/renderer/src/App.vue`、`persona/lines.ts` | 她说的那几句 |
| 测试 | `tests/test_planning.py`（11 项）、`plan.test.ts`，以及转发、预加载、事件折叠的用例；`test_agent_code.py` 的工具顺序 |
| 文档 | 两个 README、`AGENTS.md`、测试计划 13.18 |

## 测试

- `aila-ai`：492 passed
- `aila-desktop`：typecheck 通过，vitest 249 passed（1 skipped，原来就跳过的）
- `aila-server`：26 passed
- 浏览器里用假模型点过：出方案 → 卡片 → 照这个做 → 两个阶段做完；`/plan` 输入；勾「每个阶段做完停一下」→ 停在第 2 阶段 →「接着做」→ 做完

## 没做 / 拿不准

- 没用真的大模型试过：她会不会把阶段分得合适、记得调用 `stage_done`，要真测（测试计划 13.18）。忘了调用时系统会提醒她，最多 5 次
- 改计划没有单独的编辑界面，靠在对话里说
- 会话列表里没显示「第 2/4 阶段」这样的进度标记（接口有 `plan` 字段，界面之后再加）
- 三个新工具让工具列表变了，所有新会话的缓存会断一次
