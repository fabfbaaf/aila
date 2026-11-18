# 11 电脑任务：完成说明

分支：`codex/11-computer-tasks`。从开始工作时最新的 `main`（`4df4ec9`，已合并 B 长任务）创建独立 worktree；没有合并或推送。日期：2026-11-17。

## 做了什么

- 新增 `computer` 会话，从 `Path.home()` 开始，不用选项目。桌面新任务、`aila --computer`、`POST /agent/sessions {"kind":"computer"}` 都能创建；旧日志默认仍是项目会话。电脑任务拒绝自动权限和隔离分支，接口、终端、桌面及执行前都把关。
- 复用 B 的整套长任务：只读出 2～8 阶段方案 → 用户批准 → 分阶段推进 → 每阶段停一下 / 打断 / 花到上限 → 服务重启后手动继续。电脑的方案指令围绕文件、程序和脚本接口，批量操作先列精确清单，等用户看过确认，提前说明移动改名无法撤销。
- 普通读、列目录、搜索和明显只读命令不用问；普通修改询问，可允许本会话的普通文件修改；普通命令可按原文记住。电脑命令单独放 `data/computer_rules.json` 的 `computer` 范围，在设置和终端 `/rules` 中看、删，不和项目混用。
- 危险命令、受保护位置每次询问且不能记住。保护 AppData（包括其他用户目录下的 AppData）、SSH/GPG、常见浏览器资料、密码库、艾拉 `.env` 和数据目录；目录遍历默认跳过保护目录与链接。确认后重新检查权限和目标，避免等确认期间切只读、换链接或保护状态变化后仍写入。
- 电脑任务固定 PowerShell，只挂文件、命令、待办和计划工具；项目记忆、代码检索、助手和 MCP 不接入电脑任务。预算、压缩、普通文件撤销仍可使用，包含用户目录外明确指定的普通文件。
- 新增对话技能「电脑任务」，默认开，先用现有确认气泡确认，再开任务页先出方案；新增「电脑操作」，默认关，只有一个 PowerShell 工具，小事直接在对话做，只读直接运行，其他操作逐条确认，不继承已记住的任务命令。
- 会话创建时固定电脑环境快照：系统、用户和常用目录；默认附程序名、PATH 工具名及不足 1500 字的 Windows 写法。开始菜单只取快捷方式名称、不读取内容；探测最多约 1.5 秒、600 个目录项、80 个程序，超时或出错少写。设置开关默认开，可以预览同一生成逻辑的原文，保存后只影响新会话。现有快照和系统提示词不回写。
- 新增主窗口专用的创建 / 预览 IPC 校验，修改电脑概况开关也只认主窗口。电脑命令确认按钮和规则列表都写明「电脑」范围。

## 改了哪些文件

以下路径相对仓库根目录，括号标明新文件。

| 文件 | 改动 |
|---|---|
| `aila-ai/app/computer.py`（新） | 快速、有限量的环境探测、PowerShell 选择、电脑规则和 Windows 写法 |
| `aila-ai/app/runtime/computerpaths.py`（新） | 受保护路径和命令引用的判断 |
| `aila-ai/app/runtime/permissions.py`、`workspace.py`、`tools.py`、`loop.py` | 独立电脑权限、可访问绝对路径的文件工具、代码生成确认详情、确认后重查；项目的规则分支保持原样 |
| `aila-ai/app/runtime/runner.py`、`session.py`、`snapshots.py` | 会话类型、电脑计划接线、环境快照、预算/压缩复用、用户目录外普通文件撤销 |
| `aila-ai/app/tools/computer.py`（新） | 单条电脑命令技能与多步任务交接技能 |
| `aila-ai/app/config.py`、`deps.py`、`main.py`、`routes/settings.py`、`routes/agent.py`、`routes/computer.py`（新） | 设置保存与热更新、技能注册、预览接口、会话和独立规则接口 |
| `aila-ai/app/cli/app.py`、`backend.py`、`commands.py`、`render.py` | `--computer`、续接筛选、两种权限、电脑规则和显示文字 |
| `aila-ai/tests/test_computer_tasks.py`、`test_computer_permissions.py`、`test_computer_environment.py`（均新） | 电脑 Runtime / API / CLI / 长任务的 Scripted 集成、权限和路径边界、探测和设置、对话确认 |
| `aila-desktop/src/shared/agent.ts`、`settings.ts` | 共用会话类型、环境预览和设置类型 |
| `aila-desktop/src/main/agent.ts`、`agent.test.ts`、`index.ts`、`agentCreateIpc.ts` / `agentCreateIpc.test.ts`（后两者新） | 转发电脑创建与预览、主窗口和参数边界 |
| `aila-desktop/src/preload/index.ts`、`index.test.ts` | 固定的电脑预览桥与创建参数 |
| `aila-desktop/src/renderer/src/agent/AgentApp.vue`、`AgentApp.test.ts`、`ToolCard.vue`、`sessions.ts` / `sessions.test.ts`、`source.ts` / `source.test.ts` | 新任务选电脑、模式限制、电脑标签、计划/预算/撤销保留、确认授权范围 |
| `aila-desktop/src/renderer/src/settings/sections/AgentSection.vue`、`settings/AgentSection.test.ts`、`settings/computer.ts` / `computer.test.ts`（后两者新） | 环境开关、预览、失败恢复、独立电脑命令的查看删除 |
| `aila-ai/.env.example`、`aila-ai/README.md`、`aila-desktop/README.md`、`docs/real-test-plan.md` | 设置说明、入口和权限、限制、电脑任务手动测试第 18 节 |
| `docs/tasks/11-computer-tasks-result.md`（新） | 本说明 |

没有修改 `runtime/prompt.py` 的 `SYSTEM_PROMPT`，没有修改现有对话技能文件，没有改编程任务的权限规则。没有新增依赖。

## 测试

按 `AGENTS.md` 跑完三套完整测试（2026-11-17）：

| 项目 | 命令 | 结果 |
|---|---|---|
| `aila-ai` | `.venv/Scripts/python -X utf8 -m pytest -q -p no:cacheprovider` | **539 passed，10 skipped**，122.21 秒 |
| `aila-desktop` | `npm.cmd run typecheck` | 通过 |
| `aila-desktop` | `npx.cmd vitest run` | **263 passed，1 skipped**，39 个测试文件通过 |
| `aila-server` | `mvnw.cmd test`，使用 `JAVA_HOME=C:/java` | **26 passed**，0 失败 / 错误 / 跳过 |
| 额外构建 | `npm.cmd run build` | 通过 |
| 差异检查 | `git diff --check` | 通过；文本保持 UTF-8 / CRLF |

Python 使用不存在的临时 `AILA_ENV_FILE` 和本机测试 `NO_PROXY`，没有读取真实 `.env`。跳过项来自原有语音 / 向量资源等可选测试和云同步 E2E，没有为本任务新增跳过。首轮全量中既有浏览器测试随机分到 Edge 禁用的 1723 端口而失败，定位后最终全量重跑通过，没有放宽浏览器安全检查或改该用例。新增回归用例也先修正了对原有会话读取行为的错误假设；项目读取行为保持基线。

最终日志保存在 worktree 旁的 `11-python-test.log`、`11-desktop-typecheck.log`、`11-desktop-vitest.log`、`11-desktop-build.log`、`11-java-test.log`，交付副本在本任务工作区的 `outputs/`。

电脑专项覆盖：不选项目和 API/CLI 创建、拒绝 auto / isolate、普通读写与命令、电脑规则隔离与删除、危险和受保护操作反复确认、只读拒写、确认期间模式/链接/保护变化、外部文件撤销、环境开关与快速失败、聊天单条命令和先确认再交任务。长任务用 Scripted 实跑了出方案只读 → 批准 → 第一阶段 → 停下 → 新 Runtime 读日志 → 第二阶段确认写入 → 全部完成 → 撤销，以及预算停下 / 放开继续和自动压缩后保留环境。

桌面用真实 Vue 组件的内存渲染器测试新建、切模式、方案卡片和阶段、电脑命令授权范围、环境开关与预览失败恢复、规则删除；还测了主进程 IPC 拒绝其他窗口及非法参数。

## 没做 / 拿不准

- 不做界面自动化，不能点击微信等程序的按钮；只有命令行、脚本接口或 COM 可用的程序能操作。Office COM 需要本机装好相应桌面版 Office；开始菜单 / PATH 没探测到不能当成没安装。
- 没用真实大模型操作用户文件、打开记事本或调用 Office；真实流程的可用性和计划质量需按 `docs/real-test-plan.md` 第 18 节，在测试文件副本上手动验收。自动测试里的系统修改、程序启动等使用命令替身，没有真的关机、装卸软件、改注册表或读取真实登录资料。
- 先给批量操作清单、等用户看过确认属于固定电脑规则和计划指令；命令执行本身仍由权限确认把关，没有新增文件搬移事务或回滚器。用户看确认时需要核对具体命令。
- 「撤销这一轮」只恢复普通文件工具留下的内容底片；受保护文件不留底、不自动撤销。PowerShell / Office / 程序的副作用，命令移动、改名、删除，都无法自动恢复。
- Windows 没有系统沙箱。保护和危险命令识别基于路径与命令文字，已对动态变量、通配、递归和链接保守处理，但间接运行的脚本、快捷方式或外部程序可能访问文本中没有出现的目标。这不是操作系统级隔离。
- 环境预览是当前时刻重新生成的文本；创建任务时再生成一次快照。如果程序或环境恰好变了，两次结果可能不同。开关关闭后仍保留电脑任务规则与基本 shell 信息，以便执行权限和批量确认规则不变。
