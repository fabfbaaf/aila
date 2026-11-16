# 06 Windows 安装包

**先决条件：05（桌面端看管 AI 服务）合并以后再开始。** 这个任务要填 05 留下的 `packagedCommand()`。

## 背景

现在只能从源码跑：装 Python 3.12 建虚拟环境、装 Node 依赖、两个终端分别启动。这是「从四个窗口到一个应用」方案的第 4 期：做一个 Windows 安装包，双击装好、开始菜单里点「艾拉」就能用；AI 服务打包在里面，由桌面端启动（05 的 `ServiceKeeper`）。

相关代码：

- 桌面端用 electron-vite 构建（`aila-desktop/package.json` 的 `build`，产物在 `out/`）
- AI 服务入口 `aila-ai/app/__main__.py`；它的文件位置都能用环境变量改：`AILA_ENV_FILE`（.env，存用户填的密钥）、`AILA_DATA_DIR`（会话、记忆索引、代码库索引）、`AILA_MODELS_DIR`（语音模型，约 1.5GB）、`AILA_MCP_CONFIG`
- 语音模型下载脚本 `aila-ai/scripts/download_models.py`；手势要的文件由 `aila-desktop/scripts/setup-gestures.mjs` 放进 `src/renderer/public/mediapipe/`

## 要做的

1. AI 服务打成一个目录（PyInstaller，`--onedir`，不用 `--onefile`：启动快、杀毒软件误报少）：
   - 新脚本 `aila-ai/scripts/build_service.py`：调用 PyInstaller，产物 `aila-ai/dist/aila-ai/aila-ai.exe`。PyInstaller 只加进 `requirements-dev.txt`（运行时不需要）
   - 入口就是 `python -m app` 做的事；所有运行时要的数据文件要带上（`sherpa_onnx`、`fastembed`、tree-sitter 各语言、`rapidocr_onnxruntime` 自带的模型、`trafilatura` 的设置文件……），一个个试出来，写进脚本里的清单并注释为什么要它
   - `playwright` 只用它的驱动去开本机的 Edge / Chrome，不打包浏览器
   - 打好以后脚本自己冒烟测试：起 `aila-ai.exe`（环境变量指向临时目录），等 `/health` 返回 ok，再关掉
2. 用户的东西不能写进安装目录。05 的 `packagedCommand()` 返回打包后的 `aila-ai.exe`，并设好环境变量：
   - `AILA_ENV_FILE`、`AILA_DATA_DIR`、`AILA_MCP_CONFIG` 指到 `app.getPath('userData')/ai/` 下面
   - `AILA_MODELS_DIR` 指到 `userData/ai/models`
   - 第一次运行时 `.env` 不存在：用安装包里带的 `.env.example` 复制一份（密钥都是空的，所以是假模型，在设置页填密钥后就是真的）
3. 语音模型不放进安装包（太大）。没下载时语音相关的状态本来就是「没启用 / 出错」，在设置页「语音」分区加一行说明和一个「下载语音模型」按钮：主进程用打包的 `aila-ai.exe` 跑下载（给 `app/__main__.py` 加一个 `--download-models` 参数，调用 `scripts/download_models.py` 里同样的逻辑；脚本本身不动，把逻辑挪进 `app/` 里一个函数，脚本改成调用它），进度显示在那一行。下载完重启服务
4. 桌面端用 electron-builder 打 NSIS 安装包：
   - 新文件 `aila-desktop/electron-builder.yml`；`package.json` 加 `dist` 脚本（先 `build`，再打包）
   - 产品名「艾拉」，安装给当前用户（不需要管理员），开始菜单和桌面快捷方式
   - `extraResources` 带上 `aila-ai/dist/aila-ai/` 和 `.env.example`；手势要的 `public/mediapipe/` 已经在渲染层产物里，确认打进去了
   - **不要**打包 `aila-desktop/models/` 里的临时 VRM 模型（开发期占位，正式版要换原创形象）：没有模型时显示简易占位角色，这个行为本来就有
   - 没有代码签名证书：在 README 里说明 Windows 会弹「已保护你的电脑」，点「更多信息 → 仍要运行」
   - 图标：先用托盘那个紫色圆环（`main/trayIcon.ts`）生成一份 `build/icon.ico`（写个小脚本生成，不手画）
5. 更新文档：`aila-desktop/README.md` 加「打包」一节（前提、命令、产物在哪、多大）；根目录 `README.md` 的安装说明里加「直接下载安装包」

## 不要改

- AI 服务的业务代码（只加 `--download-models` 和把下载逻辑挪成函数）
- 05 的状态机本身（只填 `packagedCommand()`）
- 主窗口的页面

## 验收

- `aila-ai/scripts/build_service.py` 跑完，冒烟测试通过（`/health` ok）
- `npm.cmd run dist` 产出安装包；在一个**新的 Windows 用户**（或者虚拟机）上装：开始菜单里点「艾拉」，不用装 Python、Node，她出现在桌面上，主窗口能开，设置页「运行状态」显示 AI 服务正常
- 填密钥、开一个编程任务（找个小项目）能跑通；`userData/ai/` 里有 `.env` 和会话，安装目录里没有新文件
- 卸载后安装目录删干净；`userData` 留着（下次装还在），README 里说明怎么彻底删
- 分支说明里写：安装包多大、`aila-ai` 目录多大、第一次启动到她出现用了几秒、语音模型下载用了多久
- `AGENTS.md` 里的测试全过
