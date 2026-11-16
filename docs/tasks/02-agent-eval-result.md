# 02 编程 Agent 评测完成说明

分支：`codex/02-agent-eval`，从最新 `main` 的 `067f222` 开始。只在独立 worktree 实现，不合并 main。

## 改动

- 每次评测复制一个小项目到临时目录，初始化 git 并提交起点，使用新的 Runtime 和临时数据目录，不碰原 fixture 或 `aila-ai/data`。
- 会话使用 auto 模式；危险命令、读密钥等需要确认的操作全部拒绝并计数。任务和检查共用超时额度，退出时关闭 Runtime，清理后台进程。
- 根据检查退出码、最终回答关键词和轮次结束原因判定结果；步数耗尽、超时、被拒绝或异常不会因检查通过而误报做成。
- 记录主 Agent 步数、工具次数、耗时、token（包括助手和压缩）、输入缓存命中率、拒绝次数、检查输出末尾 30 行、最终回答。结果逐条刷新到磁盘，单项未完成后继续下一项。
- Markdown 按任务和合计汇总成功率、平均步数/用时、总 token、加权缓存命中率和失败原因；价格环境变量填全时附费用。
- 默认使用 `get_coder_model()`，`--model mock` 不联网；支持 `--only`、`--repeat`、自定义任务和报告目录。检查命令使用当前 Python，兼容带空格路径及 PowerShell 调用，保留引号内的参数。

## 文件清单

| 文件 | 内容 |
|---|---|
| `aila-ai/scripts/eval_agent.py`（新增） | 任务校验、临时项目、Runtime 驱动、自动检查、指标、费用、报告、CLI |
| `aila-ai/tests/test_eval_agent.py`（新增） | 13 项测试：正确/错误修复、步数、关键词、拒绝、超时、汇总、价格、任务校验、Python 命令替换、批量 mock、筛选重复 |
| `aila-ai/eval/tasks/fix-cart-total/`（新增） | task.json、README.md、cart.py、test_cart.py；单文件运费 bug |
| `aila-ai/eval/tasks/add-discount/`（新增） | task.json、README.md、pricing.py、test_pricing.py；新增折扣函数和测试 |
| `aila-ai/eval/tasks/rename-product-lookup/`（新增） | task.json、README.md、catalog.py、orders.py、app.py、test_catalog.py；跨 3 文件改名 |
| `aila-ai/eval/tasks/fix-quantity-trace/`（新增） | task.json、README.md、checkout.py、test_checkout.py；调用栈定位 |
| `aila-ai/eval/tasks/locate-refund/`（新增） | task.json、README.md、orders.py、billing.py、test_billing.py；只读定位 |
| `aila-ai/.gitignore` | 忽略 `eval/results/` |
| `aila-ai/README.md` | 新增「编程 Agent 评测」，说明任务格式、运行、判定和报告 |
| `docs/tasks/02-agent-eval-result.md`（本文件） | 文件清单、验证结果和边界 |

上表各任务的 Python 文件和 README.md 都位于对应目录的 `repo/` 下，task.json 位于任务目录本身。没有修改运行时、桌面端、服务端或原任务说明。

## 测试结果

| 检查 | 结果 |
|---|---|
| `.venv\Scripts\python -X utf8 -m pytest -q -p no:cacheprovider tests/test_eval_agent.py` | 13 passed，12.82 秒 |
| 完整 Python 测试（AGENTS.md 命令） | 347 passed，10 skipped，93.26 秒 |
| `npm.cmd run typecheck` | 通过 |
| `npx.cmd vitest run` | 139 passed，1 skipped |
| `.\mvnw.cmd test` | 26 项，0 失败/错误/跳过，BUILD SUCCESS |
| `.venv\Scripts\python -X utf8 -m scripts.eval_agent --model mock` | 5 个任务全部跑完，退出码 0，生成 JSONL 和 Markdown |
| PowerShell 下 mock 单项 CLI | 检查命令运行到 pytest，未修复的失败正常记入结果 |
| `git diff --check` 和禁止改动范围检查 | 通过 |

完整 Python 测试在测试进程设置 `NO_PROXY=127.0.0.1,localhost,testserver`，排除代理干扰既有本机断网用例；Java 使用进程内 `JAVA_HOME=C:/java`（JDK 21）。复用原仓库已安装依赖，没有新增第三方依赖，没有改系统设置，没有读原仓库 `.env`。

mock 的 5 项结果为 4 项「检查没通过」、1 项「回答缺少要求的词」：假模型只读 README，不会修复代码，这符合说明对链路验证的预期；不是测试失败。报告目录已验证被 git 忽略。

## 未完成或拿不准的地方

- 本任务脚手架和验收已完成。没有调用真实大模型，当前 mock 成功率不能当成真实模型的评测成绩。
- 本次实测 Windows（默认 Git Bash，额外验证 PowerShell）；Linux/macOS 未实测。
- 既有模型资源/真实服务测试仍按原条件跳过，本任务没有增加跳过。
- 检查命令由任务作者提供；本次没有对真实模型作弊修改测试、或真实业务项目的任务质量做评测。
