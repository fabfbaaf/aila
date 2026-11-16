# 05 看管 AI 服务完成说明

分支：`codex/05-service-keeper`，从 `main` 的 `067f222` 开始，在独立 worktree 中实现。安装包任务 06 没有开始。

## 完成的行为

- `ServiceKeeper` 的进程、健康检查、计时器和状态通知都可注入；启动先检查外部服务，正常时不另起进程，也不接管或关闭外部服务。
- 自己启动的服务每半秒检查健康，60 秒硬截止（检查卡住也会超时）。正常以后每 10 秒检查，连续三次查不到才重启。崩溃按 1、3、9 秒重试，5 分钟内最多 3 次；手动重启清空计数。
- 服务/Python 位置支持环境变量优先，开发时定位相邻的 `aila-ai/.venv`，运行 `-m app`；打包入口 `packagedCommand()` 暂时返回 `null`，按说明留给任务 06。缺少环境时显示建虚拟环境和安装依赖的办法。
- `/health` 的艾拉格式、普通 HTTP 响应、非 HTTP 端口和未监听端口分别判断；端口冲突停止自动重试并说明端口号。健康请求直连，不受系统代理影响；`AILA_AI_URL` 的地址和端口也用于托管进程。
- stdout 和 stderr 分别拼接跨块的日志行，再合并到带时间的列表，只保留最后 500 行。每行和未换行缓冲都有长度上限。跨块 UTF-8 和 CRLF 都能处理，日志会隐藏常见的密钥写法、Bearer 和 URL 认证信息。
- 新增状态广播与 IPC，状态谁都能读，日志和重启仅主窗口能用。预加载只暴露这几个能力。设置页最上面的 AI 服务卡片显示状态和进程号，提供重启、最近 200 行日志、错误说明；外部服务禁用重启并解释原因。
- `whenReady` 开始托管；`will-quit` 先阻止 Electron 直接退出，最多等 3 秒关自己的进程树，再继续既有退出清理。Windows 用目标 PID 的 `taskkill /T /F`，其他系统用进程组信号。旧计时器/异步回调不会在退出后重新启动服务。

## 文件清单

| 文件 | 改动 |
|---|---|
| `aila-desktop/src/main/service.ts`（新增） | 可注入托管状态机、重试和截止计时、进程所有权、有限日志及脱敏 |
| `aila-desktop/src/main/serviceNode.ts`（新增） | 服务定位、打包入口占位、直连 HTTP/TCP 健康判断、启动/关闭进程树适配 |
| `aila-desktop/src/main/serviceIpc.ts`（新增） | 三个新 IPC 的注册和主窗口授权 |
| `aila-desktop/src/main/service.test.ts`（新增） | 17 项测试覆盖状态机、两路日志交错、健康协议分类、IPC 和路径定位 |
| `aila-desktop/src/main/index.ts` | 服务实例、开始托管、复用主窗口身份检查注册新 IPC、异步退出收尾 |
| `aila-desktop/src/preload/index.ts` | serviceStatus/serviceLogs/serviceRestart/onServiceState |
| `aila-desktop/src/shared/service.ts`（新增） | 共用状态、日志和返回类型 |
| `aila-desktop/src/renderer/src/settings/sections/StatusSection.vue` | 服务状态卡片、重启、日志展开和按时刷新、状态订阅和退订 |
| `aila-desktop/README.md` | 开发启动、服务托管、外部服务和环境变量说明 |
| `docs/real-test-plan.md` | 第 0 节增加启动、恢复、退出、外部服务和端口冲突验收步骤 |
| `docs/tasks/05-service-keeper-result.md` | 本说明 |

没有修改 `aila-ai` 代码、主窗口外壳 `renderer/src/shell/`、任务页 `renderer/src/agent/` 或任何已有 IPC 的检查规则。没有读取、打印或提交原仓库 `.env`。保留原有 CRLF 换行。

## 自动化验证

在对应子项目运行 `AGENTS.md` 的全部命令，最终均无失败：

| 验证 | 命令 | 结果 |
|---|---|---|
| AI 服务全套 | `.venv\Scripts\python -X utf8 -m pytest -q -p no:cacheprovider` | 334 passed，10 skipped，92.08 秒 |
| 桌面端类型 | `npm.cmd run typecheck` | 通过，退出码 0 |
| 桌面端全套 | `npx.cmd vitest run` | 156 passed，1 skipped，新增服务测试 17 项全过 |
| 云端全套 | `.\mvnw.cmd test` | 26 项，0 失败、0 错误、0 跳过；BUILD SUCCESS |
| 桌面端构建 | `npm.cmd run build` | 通过，主进程、preload 和渲染层均生成 |
| 差异检查 | `git diff --check` | 通过 |

Python/Node 复用本机已安装依赖。测试进程用 `NO_PROXY=127.0.0.1,localhost,::1`；Maven 使用进程内 `JAVA_HOME=C:/java`、PATH 前置其 bin（JDK 21.0.12），没有改系统设置。Python 的 10 项、桌面的 1 项是既有条件跳过，本任务没有新增跳过。

## 手动验证和未完成的地方

- **尚未执行原生 Electron 手动三项验收**：准备以 `AILA_AI_URL=http://127.0.0.1:18765`、独立数据目录、空配置和无模型密钥启动自己的 `npm.cmd run dev`，但执行工具在 CreateProcess 阶段被系统策略拒绝（`blocked by policy`）。没有启动自己的 Electron/AI 服务，没有结束任何用户进程；没有换方式绕过拒绝。
- 只通过电脑工具枚举确认当时没有可见的艾拉/Electron 窗口，没有进行键鼠操作。没有实际看到设置页「正常」，没有在任务管理器结束托管 Python，也没有实际验证应用退出后的 Python 清理。这三项仍需在允许启动桌面程序的环境按第 0 节验收。
- 自动化已验证正常启动状态迁移、退出与重试计时、只关自有进程、外部服务保留、健康超时、端口冲突、手动重置计数、日志上限、IPC 权限以及退出期间的异步竞争；进程采用假的对象，**不能代替真实 Electron 生命周期和 Windows taskkill 的手动验收**。
- `packagedCommand()` 未填符合任务要求，需要任务 06 合并实现后安装包才能自动启动内置服务。本次只在 Windows 上运行检查，Linux/macOS 分支没有实机验证。
- 日志脱敏覆盖常见凭据格式，服务仍应避免主动输出敏感内容；没有读取密钥文件来建立完整密钥表。
