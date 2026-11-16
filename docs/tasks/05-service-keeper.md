# 05 桌面端自己启动和看管 AI 服务

## 背景

现在用艾拉要开两样东西：先在终端里 `cd aila-ai; .venv\Scripts\python -m app` 启动 AI 服务（127.0.0.1:8765），再启动桌面端。服务没开时，桌面端各处只会说「AI 服务没开」。这是「从四个窗口到一个应用」方案的第 2 期：桌面端主进程负责把 AI 服务开起来、盯着它、崩了重启，关程序时一起关。

相关代码：

- 主进程入口 `aila-desktop/src/main/index.ts`（`app.whenReady()` 里建窗口、托盘；`will-quit` 里收尾）
- AI 服务地址 `aila-desktop/src/shared/chat.ts` 的 `AI_SERVICE_URL`，可以用环境变量 `AILA_AI_URL` 覆盖
- AI 服务的入口 `aila-ai/app/__main__.py`（`python -m app`），健康检查 `GET /health`（返回 `{"ok": true, "model": ...}`）
- 主窗口设置页的「运行状态」分区 `aila-desktop/src/renderer/src/settings/sections/StatusSection.vue`

## 要做的

1. 新文件 `aila-desktop/src/main/service.ts`：一个 `ServiceKeeper`，把「怎么启动进程、怎么查健康、怎么计时」都通过构造参数传进来（测试里换成假的），里面只有状态机：
   - 状态：`checking`（启动时先查一次 `/health`）→ `external`（已经有服务在跑：不是我们开的，不管它，退出时也不关）或 `starting` → `ready`；进程退出 → `crashed` → 等一会儿重启；连续失败太多 → `failed`；找不到 Python 或服务目录 → `missing`
   - 启动后每 0.5 秒查一次 `/health`，最多等 60 秒（第一次加载语音、向量模型比较慢）；超时算一次失败
   - 崩了自动重启：等 1 秒、3 秒、9 秒，5 分钟内最多 3 次，之后停在 `failed`，等用户在设置页点「重启服务」
   - `ready` 以后每 10 秒查一次健康；连续 3 次查不到当成崩了
   - 保留最近 500 行输出（stdout、stderr 合在一起，按行，带时间）
   - 退出程序时：只关自己开的进程，连同子进程（Windows 上 `taskkill /PID … /T /F`；别处 `kill`）。关之前最多等 3 秒
2. 怎么找到服务：
   - 环境变量 `AILA_AI_PYTHON`、`AILA_AI_DIR` 优先
   - 开发时（`!app.isPackaged`）：仓库里的 `aila-ai/.venv/Scripts/python.exe`（别的系统是 `.venv/bin/python`），工作目录 `aila-ai`，参数 `-m app`
   - 打包后：留一个函数 `packagedCommand()`，先返回 `null`（安装包任务 06 来填）
   - 找不到就 `missing`，说明里告诉用户怎么建（`py -3.12 -m venv .venv`，再 `pip install -r requirements.txt`）
3. 端口被别的程序占了：`/health` 返回的不是艾拉的格式（没有 `ok` 字段），或者连得上但不是 HTTP——状态 `failed`，说清楚「8765 端口被别的程序占了」
4. 接到主进程（`main/index.ts`）：`whenReady` 里开始；`will-quit` 里关。状态变化发给所有窗口（已有的 `broadcast`）。新的 IPC：
   - `service:status`（谁都能读）：状态、我们开的进程号、最近一次出错的原因
   - `service:logs`（只认主窗口）：最近的输出
   - `service:restart`（只认主窗口，和 `agent:*` 的写法一样）：手动重启，清掉失败计数；`external` 状态下不能重启（不是我们开的），返回说明
   - preload 里加对应的函数和 `onServiceState`
5. 设置页「运行状态」分区：最上面加一张卡片「AI 服务」：状态（正常 / 启动中 / 别人开的 / 停了 / 找不到）、进程号、「重启服务」按钮、「看日志」展开最近 200 行（等宽字体，能滚动）
6. 不要把环境变量、密钥打进日志：启动参数里没有密钥，服务自己读 `.env`；记录启动命令时只记 Python 路径和参数
7. 更新 `aila-desktop/README.md`（运行一节：开发时不用再手动开 aila-ai，除非想看它的终端输出）和 `docs/real-test-plan.md` 第 0 节

## 不要改

- `aila-ai` 里的任何代码（服务本身不用改）
- 主窗口的页面外壳（`renderer/src/shell/`）、任务页（`renderer/src/agent/`）
- 已有 IPC 的检查规则

## 验收

- 新测试 `aila-desktop/src/main/service.test.ts`（假的启动、假的健康检查、假的计时器）：
  - 已经有服务在跑 → `external`，不启动进程，退出时不关它
  - 正常启动：`starting` → 健康检查通过 → `ready`
  - 进程退出 → 按 1、3、9 秒重启；5 分钟内第 4 次崩溃 → `failed`；手动重启后重新计数
  - 60 秒等不到健康 → 算失败、关掉那个进程再重试
  - 端口被别的程序占了 → `failed`，原因里有「端口」
  - 找不到 Python → `missing`
  - 日志只留最后 500 行
  - 退出时只关自己开的进程
- 手动验证（写进分支说明）：关掉已经开着的 aila-ai，`npm.cmd run dev`，几十秒后设置页显示「正常」；任务管理器结束 python.exe，几秒后自动重启；退出艾拉后 python.exe 没了
- `AGENTS.md` 里的测试全过
