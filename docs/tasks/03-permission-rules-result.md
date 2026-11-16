# 03 管理记住的命令：实现与验证说明

分支：`codex/03-permission-rules`，从最新 `main` 的 `067f222` 开始，在独立 worktree 中实现。没有合并 main，没有推送。

## 实现的行为

- `Rules.all()` 返回按项目保存的命令副本，只读、不写文件；缺失或不合法的 JSON 返回空列表数据，忽略不合法的命令项。
- `GET /agent/rules` 按项目路径排序；`DELETE /agent/rules` 按项目和命令的完整原文撤销一条规则，不存在时返回 404。删除后，同一个 `Policy` 的下一次判断立即生效。
- 主窗口的设置页新增「编程」：介绍三种模式和危险命令限制，按项目展示命令，可逐条删除，带加载、空状态、断连重试、删除失败和删除后的提示。规则内容仅按文本显示，不执行。
- `agent:rules` 和 `agent:rules:forget` 只接受主窗口发送；删除还检查项目和命令的类型、非空和长度。不提供添加允许规则的接口。
- 浏览器调试下，只有新增的「编程」分区可通过 `/ai` 本机代理使用相同接口，其他设置分区保持原有的桌面能力限制。
- 终端 `/rules` 只列当前项目的规则并编号，`/rules forget 编号` 撤销一条；内置和 HTTP 后端都支持。命令原文按文字打印，参数错误不会删除规则。

## 文件清单

| 文件 | 改动 |
|---|---|
| `aila-ai/app/runtime/permissions.py` | 只新增 `Rules.all()`，未修改任何权限判断或既有读写方法 |
| `aila-ai/app/routes/agent.py` | 在文件末尾新增规则请求类型及 GET、DELETE 接口 |
| `aila-ai/app/cli/backend.py` | 协议和两个后端新增 `rules()`、`forget_rule()` 方法 |
| `aila-ai/app/cli/commands.py` | 新增 `/rules` 帮助项 |
| `aila-ai/app/cli/app.py` | 在命令分支末尾新增 `/rules` 的列表、编号删除和输入校验 |
| `aila-ai/tests/test_permission_rules.py`（新增） | 10 项测试，覆盖只读副本、错误文件、排序、原文匹配、404、即时权限变化、请求校验、Origin 限制和两种终端后端 |
| `aila-desktop/src/shared/settings.ts` | 新增 `agent` 分区及共用规则类型 |
| `aila-desktop/src/shared/shell.ts` | 设置导航新增「编程」 |
| `aila-desktop/src/shared/shell.test.ts` | 页面地址往返验证增加 `/settings/agent` |
| `aila-desktop/src/main/ai.ts` | 新增列表与删除请求函数 |
| `aila-desktop/src/main/index.ts` | 新增两个 IPC，检查发送方和删除参数 |
| `aila-desktop/src/preload/index.ts` | 新增两个有类型的规则方法 |
| `aila-desktop/src/main/permissionRules.test.ts`（新增） | 主进程请求的原文保持、404 和断连测试 |
| `aila-desktop/src/renderer/src/shell/SettingsPage.vue` | 注册新增分区，允许该分区在浏览器调试时使用本机代理 |
| `aila-desktop/src/renderer/src/settings/agentRules.ts`（新增） | 桌面 IPC / 浏览器本机代理的请求入口及错误结果 |
| `aila-desktop/src/renderer/src/settings/agentRules.test.ts`（新增） | 浏览器请求路径、删除原文、404 和断连测试 |
| `aila-desktop/src/renderer/src/settings/sections/AgentSection.vue`（新增） | 编程权限说明、分项目规则列表和删除交互；沿用现有深色设置样式 |
| `aila-desktop/src/renderer/src/settings/AgentSection.test.ts`（新增） | 在 Vue 内存渲染器中挂载真实组件，验证空状态、分项目删除、失败保留、断连刷新恢复 |
| `aila-desktop/vitest.config.ts`（新增） | 使用已有 Vue 插件编译客户端组件，支持无需真实浏览器的组件测试；未增加依赖 |
| `docs/tasks/03-permission-rules-result.md`（本文件） | 逐文件改动、验证结果及未验证范围 |

没有修改 `Policy.decide`、`Policy._command`、任务页 `src/renderer/src/agent/`、根目录 AGENTS.md 或原任务说明。没有读取或提交原仓库 `.env`。已有文件保留 CRLF。

## 测试结果

所有 AGENTS.md 要求的命令退出码均为 0，没有失败。

| 验证 | 命令 | 结果 |
|---|---|---|
| 完整 AI 服务测试 | `.venv\Scripts\python -X utf8 -m pytest -q -p no:cacheprovider` | 344 passed，10 skipped，90.32 秒 |
| 新增权限规则定向测试 | 上述命令追加 `tests/test_permission_rules.py` | 10 passed，1.73 秒 |
| 桌面类型检查 | `npm.cmd run typecheck` | 通过 |
| 完整桌面测试 | `npx.cmd vitest run` | 147 passed，1 skipped；19 个测试文件通过，1 个跳过 |
| 新增真实 Vue 组件交互 | `npx.cmd vitest run src/renderer/src/settings/AgentSection.test.ts` | 4 passed |
| 测试配置类型检查 | `npx.cmd tsc --noEmit --skipLibCheck --moduleResolution Bundler --module ESNext --target ES2022 vitest.config.ts` | 通过 |
| 完整云端测试 | `.\mvnw.cmd test` | 26 项，0 失败、0 错误、0 跳过；BUILD SUCCESS |
| 桌面构建 | `npm.cmd run build` | 主进程、预加载、渲染层全部构建通过 |
| 差异检查 | `git diff --check` | 通过 |

测试进程设置 `NO_PROXY=127.0.0.1,localhost,testserver`；Maven 使用 `JAVA_HOME=C:/java`、PATH 前置 `C:/java/bin`。Python 的 10 项跳过来自现有的向量/语音模型资源条件；桌面 1 项跳过来自未设置 `AILA_SERVER_E2E`，没有为本任务添加跳过。

## 本机调试链路与验证边界

- 已实际运行 `npm.cmd run dev:web`，在 5180 端口提供本分支渲染层。原 8765 已有服务，为避免碰原服务的数据，临时配置将本次 `/ai` 代理指向 8768 上的隔离服务；该服务使用本分支真实的 `/agent/rules` 路由、`Runtime` 和临时规则文件，没有连接真实模型。
- HTTP 实测页面和 Vue 组件模块均返回 200；经 5180 的 `/ai/agent/rules` 列出两项目共三条测试规则，DELETE 返回 `ok: true`，重新 GET 为两条，其他项目规则保留。临时进程已停止，临时源文件和测试规则已移除。
- **未完成真实浏览器中的可视检查和点击删除验收。** CUA 没有可连接的浏览器；尝试原生 Chrome 时，工具安全检查因“无法足够可靠地确认 Windows 当前浏览器 URL”停止本轮 Computer Use。已按工具要求结束该路径，没有绕过检查，没有通过该工具发送键鼠。
- Vue 内存组件测试、Vite HTTP 链路和生产构建不能替代真实浏览器的布局、焦点和点击验收；这些视觉与端到端范围仍需人工核对。Electron 真实窗口的 IPC 发送方边界本次按代码检查与类型检查验证，没有驱动真实 Electron 窗口。
- 本次实际验证平台为 Windows，未在 Linux / macOS 上运行。

实现和可自动执行的检查均已完成；没有已知实现缺陷。真实浏览器验收因上述工具限制保留为明确的未验证项。
