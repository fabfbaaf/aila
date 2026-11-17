# 08 花费和上限

分支 `codex/08-budget` 从最新本地 `main` 的 `752f504` 创建。

## 行为

- 以模型名查人民币价格，分别计算缓存命中输入、未命中输入和输出。会话金额包括所有用量事件，包含压缩和查资料的助手；每轮 `turn_end.cost` 记录本轮金额，出错或被打断时也保留已记录用量对应的费用。
- `data/prices.json` 可以覆盖或增加模型价格，格式是 `{"my-model":{"hit":0.5,"miss":2,"output":8}}`，三项单位都是元 / 百万 token。数字也可写成十进制字符串。每次汇总重读文件，修改后下次计算就生效，兼容 Windows UTF-8 BOM。金额在内部用 Decimal 汇总。
- 未配置模型、缺项、非法价格或无法读取的价格表都按未知处理。含未知用量时总金额是 `null`，保留 token，不显示成免费。仍按已知金额的小计检查上限；补齐价格才能计算和约束完整花费。
- 上限属于整个会话，可以在创建时指定，也可随时修改或取消；修改只追加 `budget` 事件。每一步开始前、自动压缩前及压缩后检查，累计已花达到上限就以 `reason: "budget"` 结束。当前一步和已派出的助手会做完，所以金额可能超过上限；助手仍最多 20 步，不单独设预算。
- 按用户确认的规则修正原验收数字：上限 0.01 元、每步 0.004 元，完成三步累计 0.012 元，**第 4 步开始前停**。调高或取消上限后说「继续」接着做；仅说「继续」且上限不变会再次停下。结束文案也据此说明先改上限再继续。
- API 支持创建时的 `budget` 和 `PUT /agent/sessions/{sid}/budget {"amount": 数字或null}`。拒绝负数、布尔值、字符串、NaN 和无穷大；非法输入不会追加事件。新写操作继续限制本机来源。
- 终端支持 `--budget`、恢复会话时显式修改、`/budget`、`/budget 金额`、`/budget off`；`/usage` 和每轮结尾显示金额。单次执行因预算停下时退出码是 1，JSON 输出保留完整事件。
- 桌面新任务可填上限，顶部显示累计花费 / 上限，点开在页内修改或留空取消。每轮结尾显示本轮金额，预算耗尽时角色说一句。未知价格有文字提示；预算 IPC 校验主窗口来源、会话 id 和金额。

## DeepSeek 价格来源与待填项

查阅日期：**2026-11-16**（本机时区）。

- [DeepSeek 官方中文价格页](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)当前只列 `deepseek-flash`、`deepseek-v4-pro` 等新模型，没有任务指定的两个旧模型名的价格行。
- [DeepSeek 官方更新记录](https://api-docs.deepseek.com/updates/)的 2026-04-24 公告说明 `deepseek-chat`、`deepseek-reasoner` 计划于 2026-07-24 停用，过渡期间映射到 V4 Flash。

未查到这两个旧模型名目前适用的三项价格，所以 `pricing.py` 中 `deepseek-chat`、`deepseek-reasoner` 的默认价格均留空，并注明上述来源和日期。需要桔梗确认账户实际使用的模型和价格后填入 `data/prices.json`；没有把新模型或历史价格套到旧名字上。

## 改动文件

| 文件 | 内容 |
|---|---|
| `aila-ai/app/runtime/pricing.py` | 默认空价格、用户价格表、严格校验、费用计算和预算数值校验 |
| `aila-ai/app/runtime/session.py`、`loop.py` | 会话和本轮金额、最新上限、压缩前后和每步开始前的预算检查 |
| `aila-ai/app/runtime/runner.py`、`subagent.py` | 创建和修改上限、发布追加事件、助手继承同一价格表 |
| `aila-ai/app/routes/agent.py` | 新建预算参数和修改预算接口，非有限输入返回 422 |
| `aila-ai/app/cli/app.py`、`backend.py`、`commands.py`、`render.py` | 命令行参数、双后端、斜杠命令、金额和停止原因 |
| `aila-ai/tests/test_runtime_budget.py` | 使用测试价格和剧本模型验证计费、边界、恢复、API 和终端 |
| `aila-desktop/src/shared/agent.ts`、`budget.ts` | 共用预算事件和会话字段、金额校验与显示 |
| `aila-desktop/src/main/agent.ts`、`agentBudgetIpc.ts`、`index.ts` | HTTP 转发、预算进展和主窗口专用 IPC |
| `aila-desktop/src/preload/index.ts` | 预加载预算桥 |
| `aila-desktop/src/renderer/src/agent/AgentApp.vue`、`BudgetControls.vue`、`source.ts`、`fold.ts` | 新任务预算、顶部编辑、事件回放及每轮金额 |
| `aila-desktop/src/renderer/src/App.vue`、`persona/lines.ts` | 角色预算停止提示 |
| `aila-desktop/src/shared/budget.test.ts`、`src/main/agentBudgetIpc.test.ts`、`src/main/agent.test.ts`、`src/preload/index.test.ts`（桌面端） | 金额格式、主窗口限制、参数校验、请求转发及预加载 |
| `aila-desktop/src/renderer/src/agent/agent.test.ts`、`AgentApp.test.ts`、`BudgetControls.test.ts`、`source.test.ts`（同目录） | 每轮金额、预算原因、事件回放、表单保存与切换、浏览器和桌面两种来源；运行中修改预算不丢事件，金额输入不触发工具确认快捷键 |
| `aila-ai/README.md`、`aila-desktop/README.md`、`docs/real-test-plan.md` | 使用方法、价格表格式、停止规则、未知价格及第 12/13 节人工验收项 |
| `docs/tasks/08-budget-result.md` | 本说明 |

用量事件的写法、上下文顺序和 07 的撤销逻辑均保持原样。

## 验证

| 命令 | 结果 |
|---|---|
| `.venv\Scripts\python -X utf8 -m pytest -q -p no:cacheprovider` | 437 通过，10 项按既有条件跳过（103.34 秒） |
| 新预算测试及既有终端定向测试 | 39 通过，其中新预算文件 29 项 |
| `npm.cmd run typecheck` | 通过 |
| `npx.cmd vitest run` | 230 通过，1 项按既有条件跳过；34 个测试文件通过 |
| `.\mvnw.cmd test` | 26 通过，0 失败，0 错误，0 跳过 |
| `npm.cmd run build` | 主进程、预加载和渲染层构建通过 |

Python 的 10 项跳过来自既有向量和语音模型测试，预算测试全部执行。桌面跳过的是需要真实服务器的云同步端到端测试，未设置 `AILA_SERVER_E2E`。Python 测试进程设置 `NO_PROXY=127.0.0.1,localhost,testserver`，Java 使用本机 JDK 21，均仅影响当次进程环境。测试使用临时数据，不读取用户密钥。

桌面回归额外覆盖：预算 HTTP 回执先于实时流时不跳过中间的工具、用量和结束事件；保存期间切换会话不被旧回执拉回；两个预算输入入口里的 `1`、`2`、`Esc` 不触发工具授权或打断，编辑表单的 Esc 仍能取消。独立只读复核后未发现剩余阻塞问题。`git diff --check` 通过，文本文件保留 UTF-8 / CRLF。

## 验证边界

- 未调用真实收费模型，自动化使用临时价格表和剧本模型。未在真实 Electron 窗口手点，也未在其他操作系统运行。
- 金额依赖模型返回的用量和当前价格表，不是供应商账单；更改价格表会重新计算会话总额，已经写入的每轮结尾金额保留当时的值。未知价格不能提供完整预算保护。
- 预算检查在两步之间生效，已经执行的一步、同批助手或手动压缩可能超过上限；它们的用量会累计，下一步检查能够看到。预算 0 会阻止下一步，即便模型价格未知。
- 待确认的是上述两个旧 DeepSeek 模型名的适用价格，按任务允许的方式留空。其余功能和自动验收均在本分支完成。
