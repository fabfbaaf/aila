# 08 花了多少钱、花到上限就停

**先决条件：07（撤销一轮）合并以后再开始。** 两个任务都要改 `runner.py`、`routes/agent.py`、终端命令和任务页，排在一起做冲突太多。

## 背景

省钱是这个项目最突出的地方：上下文按 DeepSeek 的前缀缓存排（见 `aila-ai/README.md`「编程 Agent 运行时」），每一步都记了用量（`usage` 事件：`prompt`、`completion`、`cache_hit`、`cache_miss`，子 Agent 的用量带 `purpose: "explore"`）。但界面和终端只显示 token 数，看不出花了多少钱；一个任务失控（来回改、来回跑测试）时也没有刹车。

## 要做的

1. 价格表：新文件 `aila-ai/app/runtime/pricing.py`
   - 按模型名记三个价格（元 / 百万 token）：命中缓存的输入、没命中的输入、输出。先填 `deepseek-chat` 和 `deepseek-reasoner`，数字照 DeepSeek 官网的价格页填，注释里写来源网址和查的日期；查不到就留空并在分支说明里写出来，让桔梗填
   - 用户可以在 `data/prices.json` 里覆盖或者加别的模型（格式写进 README）
   - `cost(usage_event) -> float | None`：不认识的模型（包括假模型 `mock-coder`）返回 `None`，界面上就只显示 token，不瞎估
2. 会话的花费：`Session.cost`（所有 `usage` 事件加起来，包括压缩和子 Agent 的），`Session.summary()` 里加 `cost`；每轮结束的 `turn_end` 事件里加这一轮的 `cost`
3. 上限：
   - 新事件 `budget`（`{"amount": 元}`，`null` 表示不限），像 `mode` 一样只追加；`Session.budget` 取最近一条。开会话时可以带上（`Runtime.create(..., budget=…)`），之后随时改
   - `Agent.turn` 每一步开始前（压缩之前也算）看一眼：已经花到上限就停，`turn_end` 的 `reason` 是新的 `"budget"`。停在两步之间，日志本来就是完整的（和 `max_steps` 一样）
   - 用户调高上限后说「继续」就接着做
   - 查资料的助手（`subagent.py`）一次最多 20 步，不单独限；它花的算进会话里，下一步开始前就会被看到
4. 入口：
   - 接口：`POST /agent/sessions` 多一个可选的 `budget`；新的 `PUT /agent/sessions/{sid}/budget`（加在 `routes/agent.py` 末尾）
   - 终端：`aila --budget 0.5`；`/budget 1`（改上限）、`/budget off`（不限）；`/usage` 多显示钱；每轮结束那一行加「¥0.06」（`cli/render.py` 只改结束那一行和 `turn_end` 原因的说明）
   - 桌面：新任务的选项里加「花费上限（元，可以不填）」；任务页顶上显示「已花 ¥0.12 / 上限 ¥0.50」，点一下在页面里改（不用系统对话框）；每轮结尾那一行加钱（`fold.ts` 的 `endStats`）；`reason === 'budget'` 时那一行写「花到上限了：调高上限，或者说『继续』」
   - 桌面上的她：停在上限时说一句（`persona/lines.ts` 加一句，照 `agentError` 的写法接进 `App.vue` 现有的进展处理）
5. 更新 `aila-ai/README.md`、`aila-desktop/README.md`、`docs/real-test-plan.md`（第 12、13 节各加一条）

## 不要改

- 用量是怎么记的（`loop.py` 里 `usage` 事件的写法）、上下文怎么排
- 07 加的撤销逻辑

## 验收

- 新测试 `aila-ai/tests/test_runtime_budget.py`（`Scripted` 模型，每步返回固定的 `Usage`，用一张测试用的价格表）：
  - 钱算得对：命中、没命中、输出分开算；子 Agent 和压缩的用量也算进去
  - 不认识的模型 `cost` 是 `None`，花费显示为空而不是 0
  - 上限 0.01 元、每步花 0.004 元：做到第 3 步开始前停下，`reason == "budget"`；日志推出来的消息是合法的
  - 调高上限后接着做；`budget` 为 `null` 时不限
  - 接口和终端命令能设、能改
- 桌面：`fold.ts` 的测试覆盖每轮的钱和 `budget` 原因的文字
- `AGENTS.md` 里的测试全过
