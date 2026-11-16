# 01 后台命令：开发服务器这类不会自己退出的命令

## 背景

编程 Agent 的 `run_command`（`aila-ai/app/runtime/tools.py` 的 `CodingTools._command`，底层是 `runtime/shell.py` 的 `run`）每次开一个新进程、等它结束，超时（默认 120 秒）就杀掉整棵进程树。所以她没法启动开发服务器（`npm run dev`、`uvicorn ...`、`python -m http.server`）再去访问它、看日志。系统提示词（`runtime/prompt.py`）里现在写着「不会自己退出的命令现在还不支持」。

## 要做的

1. `run_command` 加一个可选参数 `background`（布尔，默认 false）。为 true 时：
   - 用和前台一样的 shell、工作目录、环境变量（`shell.clean_env()`，去掉艾拉自己的密钥）启动，不等它结束
   - 等最多 3 秒（或者输出里出现 `ready`、`listening`、`started`、`http://` 这类字样就提前返回），把这段时间的输出和一个任务号（`job-1`、`job-2`……）返回给模型
   - 一个会话最多同时 3 个后台任务，超了返回说明，让她先停掉一个
2. 两个新工具（接在 `CodingTools._build()` 现有工具列表的最后面，顺序固定）：
   - `job_output`：参数 `id`、可选 `tail`（最后几行，默认 40）。返回状态（还在跑 / 已退出，退出码）、上次看过之后的新输出（有上限）、总共输出了多少。kind 是 `read`（不用问）
   - `job_stop`：参数 `id`。杀掉整棵进程树（用 `shell.kill_tree`），返回最后几行输出。kind 是 `meta`（她自己开的进程，停掉不用问）
3. 输出：后台读进程输出的协程把内容放进有上限的缓冲（开头留 4000 字、结尾留 20000 字，中间省略并记总字节数），解码沿用 `shell.decode`（UTF-8，不行就 GBK），去掉颜色控制符
4. 生命周期：
   - 后台任务跨轮次保留（开发服务器一直开着，下一轮还能看）
   - 运行时关闭（`Runtime.shutdown`）时杀掉所有会话的所有后台任务
   - 用户打断一轮时**不**杀后台任务（打断的是她这一轮，不是她开的服务器）
5. 权限：`background: true` 的命令和普通命令走同一套权限（`Policy.decide("command", command)`），危险命令照样要问
6. 更新：
   - `runtime/prompt.py` 系统提示词里关于长时间命令的那句：改成说明怎么用 `background`、`job_output`、`job_stop`，用完记得停掉
   - 工作窗口的工具卡片标题：`aila-desktop/src/renderer/src/agent/fold.ts` 的 `actionOf` 加 `job_output`、`job_stop` 两个分支（「看后台任务 job-1 的输出」「停掉后台任务 job-1」）；`run_command` 带 `background` 时标题前加「后台：」
   - 终端版：`aila-ai/app/cli/render.py` 的 `_action` 同样加这两个分支（只改这个函数）
   - `aila-ai/README.md`「编程 Agent 运行时」的工具列表和「还没做」那一条

## 不要改

- 权限规则本身（`permissions.py`）
- 工作窗口和终端的其他部分（另一个任务在改）

## 验收

- 新测试文件 `aila-ai/tests/test_runtime_jobs.py`（用真的子进程，跑测试用的 python：`sys.executable`）：
  - 后台启动一个先打印 `ready` 再睡 60 秒的脚本：几秒内返回，结果里有 `ready` 和任务号
  - `job_output` 看得到后来打印的内容；进程退出后状态变成「已退出」带退出码
  - `job_stop` 之后进程确实没了（Windows 上用 `tasklist` 或 `psutil` 以外的办法验证也行，比如 `proc.returncode`）
  - 第 4 个后台任务被拒绝
  - `Runtime.shutdown()` 后所有后台任务都被杀掉
  - 打断一轮（`rt.interrupt`）后后台任务还在
  - 后台命令照样受权限约束：先问再改模式下要确认，危险命令（比如 `rm -rf x &`）要确认
- `AGENTS.md` 里的测试全过
