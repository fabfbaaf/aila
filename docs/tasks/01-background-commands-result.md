# 01 后台命令完成说明

分支：`codex/01-background-commands`。从本地 `main` 的 `e87b7a5` 开始，包含任务说明提交 `ccfbf94`。在独立检出中实现，避免混入原仓库已有的未提交改动。

## 完成的行为

- `run_command` 增加可选布尔参数 `background`，默认关闭；后台启动沿用前台的 shell、工作目录、环境变量清理和权限判断。
- 后台启动最多等 3 秒，遇到 ready、listening、started、http:// 或完整退出时提前返回任务号和启动输出。每会话最多同时 3 个任务，并发启动也不能超额。
- 新工具固定接在基本工具列表末尾：`job_output` 不需确认，返回状态、退出码、新输出、累计字节数；默认最后 40 行。`job_stop` 不需确认，停止进程树并返回最后输出。
- 输出缓冲保留开头 4000 字、结尾 20000 字；中间省略并报告总字节数。增量解码支持 UTF-8 / 系统编码 / GBK，跨读取的半个汉字保留到下一次；颜色控制符跨块也会去掉。
- 后台任务跨轮保留，用户打断当轮时继续运行；`Runtime.shutdown()` 清理所有会话。
- Windows 先挂起根进程，加入系统 Job Object 后再恢复；即使父进程先退出、子进程重定向了输出，也能正确计数、查询状态和清理。仍调用 `shell.kill_tree`；不增加第三方依赖。

## 文件清单

| 文件 | 改动 |
|---|---|
| `aila-ai/app/runtime/shell.py` | 会话后台任务管理、启动等待、有限输出缓冲、增量解码、查看和停止 |
| `aila-ai/app/runtime/winjobs.py`（新增） | 标准库 ctypes 封装 Windows Job Object，跟踪并清理整棵进程树 |
| `aila-ai/app/runtime/tools.py` | background 参数、job_output/job_stop 工具、参数与错误提示 |
| `aila-ai/app/runtime/runner.py` | shutdown 时清理各会话后台任务 |
| `aila-ai/app/runtime/prompt.py` | 说明常驻命令如何启动、查看、停止 |
| `aila-ai/app/cli/render.py` | 只改 `_action`，增加后台前缀和新工具标题 |
| `aila-ai/README.md` | 更新工具、打断和生命周期说明，移除后台命令未实现条目 |
| `aila-ai/tests/test_runtime_jobs.py`（新增） | 25 项真实子进程测试，覆盖验收、并发、拆字、进程树和权限 |
| `aila-ai/tests/test_cli_views.py` | 终端重放的后台工具标题测试 |
| `aila-desktop/src/renderer/src/agent/fold.ts` | 只改 `actionOf`，增加后台前缀和新工具标题 |
| `aila-desktop/src/renderer/src/agent/agent.test.ts` | 工具卡片标题测试 |
| `docs/tasks/01-background-commands-result.md`（本文件） | 改动、验证结果和验证边界 |

没有修改 `permissions.py`、其他工作窗口组件、终端的其他函数、原来的四份任务说明或根目录 AGENTS.md。

## 测试结果

所有命令都在各自子项目目录执行，最终结果无失败。

| 验证 | 命令 | 结果 |
|---|---|---|
| 完整 AI 服务测试 | `.venv\Scripts\python -X utf8 -m pytest -q -p no:cacheprovider` | 327 passed，10 skipped，75.55 秒 |
| 后台任务定向测试 | 同上，追加 `tests/test_runtime_jobs.py` | 25 passed，15.96 秒 |
| 桌面类型检查 | `npm.cmd run typecheck` | 通过，退出码 0 |
| 完整桌面测试 | `npx.cmd vitest run` | 131 passed，1 skipped，退出码 0 |
| 完整服务端测试 | `.\mvnw.cmd test` | 26 项，0 失败、0 错误、0 跳过，BUILD SUCCESS |
| PowerShell 兼容验证 | 设置 `AILA_SHELL` 为 Windows PowerShell 后，运行后台测试中的启动、父先退出、shutdown、interrupt 用例 | 7 passed，18 deselected |
| 差异检查 | `git diff --check` | 通过 |

执行环境说明：

- Windows，Python 3.12.3；完整测试默认使用 Git Bash。复用原仓库已安装的 Python / Node 依赖。
- Python 测试进程设置 `NO_PROXY=127.0.0.1,localhost,testserver`。首轮现有本机断网用例被代理返回 503；只对本机地址绕过代理后，完整测试通过，没有修改该用例或错误分类代码。
- Java 在测试进程中设置 `JAVA_HOME=C:/java`，使用 JDK 21.0.12，避开默认 PATH 中无法正常启动的 Oracle Java 跳转程序。
- 真实子进程及其清理、Vitest 缓存和 Maven 缓存需要提升工具执行权限；未修改系统配置。
- 保留原有 CRLF 换行；未读取或提交原仓库的密钥文件。

## 未完成或拿不准的地方

- 任务说明中的功能和验收已完成，没有已知遗留缺陷。
- 本次实际验证平台为 Windows，未在 Linux / macOS 上执行测试。
- Python 的 10 项、桌面的 1 项跳过来自既有测试条件，未为本任务新增跳过；需要模型资源或真实服务的验证不包含在本次结果里。
- 后台进程只在当前运行时内保留，不支持服务重启后恢复；任务要求的跨轮保留已验证。
