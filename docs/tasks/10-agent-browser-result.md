# 10 编程任务里的浏览器

分支 `codex/10-agent-browser` 从 `main` 的 `d8279c9` 建立。

## 交付

- 编程任务能打开、查看和操作网页，也能按键、列出或管理本会话的标签页、选择项目内的文件上传、对页面或单个元素截图。各会话使用同一个浏览器进程里的独立浏览器上下文。工具顺序固定在 `remember` 和 MCP 工具之前。
- 本机端口第一次访问前显示具体地址和端口，用户可以同意这一会话使用，或为该项目记住该端口。请求、重定向和浏览器网络连接检查目标地址。AI 服务端口（包括配置值和默认 8765）、云端 8790、其他本机别名和局域网都拦截。
- 本机页面的操作按任务权限模式处理。公网页面的写入类操作在执行前单独确认。确认内容由页面状态和工具参数生成。敏感输入框不能操作，上传文件经过工作区、路径和密钥检查。
- `browser_screenshot` 把 PNG 写到会话的 `.shots` 目录，工具结果引用文件名。模型拿到路径和页面概要。桌面任务卡片里的「看截图」经主窗口专用 IPC 读取，并校验文件确实在会话目录内。会话删除时一起关闭标签页、清理截图。
- 单个浏览器工具最多等待 45 秒。超时会返回错误，提示先重新读取页面核对已完成的操作，随后由任务决定下一步。

## 改动文件

| 文件 | 改动 |
|---|---|
| `aila-ai/app/browser/session.py`、`proxy.py`、`snapshot.py` | 编程会话与浏览器进程、路由和连接地址校验、端口控制、按键、标签页、上传、截图及元素检查 |
| `aila-ai/app/runtime/browsertools.py`、`permissions.py`、`loop.py` | 组装浏览器工具、生成确认预览、应用本机端口和不同模式的权限 |
| `aila-ai/app/runtime/runner.py` | 仅在 `Runtime.agent()` 工具列表组装处接入 |
| `aila-ai/app/runtime/tools.py`、`shell.py`、`session.py` | 接入已有的会话关闭过程，清理标签页与截图 |
| `aila-ai/app/runtime/prompt.py` | 编程提示词加入前端验收和把网页内容当数据的说明 |
| `aila-ai/tests/test_runtime_browser.py`、`test_agent_code.py` | 浏览器工具、权限、隔离、超时和清理验收，更新加入浏览器后的工具顺序断言 |
| `aila-desktop/src/main/agentScreenshotIpc.ts`、`index.ts`、`src/preload/index.ts`（桌面端） | 截图读取、主窗口校验与预加载桥 |
| `aila-desktop/src/renderer/src/agent/AgentApp.vue`、`ToolCard.vue`、`fold.ts`、`source.ts`、`src/shared/agent.ts`（桌面端） | 端口确认按钮文案、截图事件、卡片预览 |
| `aila-desktop/src/main/agentScreenshotIpc.test.ts`、`src/renderer/src/agent/AgentApp.test.ts`（桌面端） | IPC 的窗口及路径边界，以及真实 Vue 卡片的展开预览 |
| `aila-ai/README.md`、`aila-desktop/README.md`、`docs/real-test-plan.md`、本结果说明 | 使用方法和验收记录 |

## 本机 Vite 验收

在 Windows 的临时 Vite 项目里，用剧本模型经真实 `Runtime` 读取并修改 `index.html`，把「旧按钮文字」改成「由艾拉更新的按钮」。运行时用后台命令启动 Vite 5.4.21。脚本收到和确认卡片共用的事件，核对并记住本机 **8730** 端口。随后由本机 Edge（无头模式）打开页面、读到新按钮、点击后看到「已点击新按钮」并截图。

第二个会话在同一项目无须重复确认端口，页面仍显示自己的「等待点击」。删除会话后，原截图目录已清理，后台服务器随该会话停止。

截图原位置为 `data/sessions/20261117-030407-c0674d.shots/09be158356c947a394865e45b2af1d95.png`，交付副本为任务输出目录的 `10-agent-browser-vite.png`。

另用真实 Edge 和临时本机 HTTP 服务验证了两个会话共用进程、独立标签和弹出页归属、上传、按键、敏感输入拦截、GET 表单及截图。跨端口两跳重定向、脚本请求和 WebSocket 均未抵达受保护服务器。

## 测试

| 命令 | 结果 |
|---|---|
| `.venv\Scripts\python -X utf8 -m pytest -q -p no:cacheprovider` | 483 通过，10 项既有条件跳过（122.06 秒） |
| `tests/test_runtime_browser.py` | 全量中 12 项通过 |
| `npm.cmd run typecheck` | 通过 |
| `npx.cmd vitest run` | 246 通过，1 项既有条件跳过 |
| `.\mvnw.cmd test` | 26 通过，0 失败，0 跳过 |
| `npm.cmd run build` | 主进程、预加载、渲染层均构建通过 |

Python 的 10 项跳过来自缺少可选模型的既有用例，桌面的一项跳过来自未配置专用服务器的云同步端到端用例。测试进程使用不存在的 `AILA_ENV_FILE`、既有测试夹具需要的 `LLM_MODEL=deepseek-chat`，并设置本机地址的 `NO_PROXY`。Java 使用本机 JDK 21。

## 验证边界

- 可选的模型看图功能未实现。模型接收截图位置和页面文字；图片可在桌面任务卡片中查看。
- 真实浏览器验收使用本机 Edge、临时项目和剧本模型。桌面卡片及文件读取通过组件和 IPC 自动化验证。
- 本机端口许可适用于整个会话。该会话里打开的网页可以请求已授权的开发端口。选择记住后可在同一个项目的新会话复用，新的会话仍各自决定自己的访问范围。
- 截图预览适用于 Electron 桌面端。Vite 验收是在无头 Edge 和剧本模型上完成，Electron 的卡片及文件读取由自动化测试验证。
