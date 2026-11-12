# 艾拉 · 程序员的 AI 桌面伙伴

常驻桌面的 3D 数字人。她会看着你的鼠标、被戳会害羞；叫一声「你好艾拉」就能和她说话，她出声回答，口型、表情和动作跟着声音走。
目标是成为程序员的工作伙伴：语音唤醒、上网办事、看到报错主动帮忙，以及用手势操控代码的「魔法工作台」。

## 当前进度

- [x] 透明置顶桌面窗口，鼠标只在角色身上生效，其他地方穿透到桌面
- [x] VRM 3D 角色：待机呼吸眨眼、视线跟随全屏鼠标、点击反应、拖动贴边、犯困
- [x] 文字对话：流式回复按说话节奏输出，表情和动作与对应句子同步，可打断
- [x] 联网搜索和网页阅读：她自己判断要不要查，查的时候气泡显示进度；读链接有 SSRF 防护（开着代理的 fake-ip 模式也拦得住）
- [x] 语音：唤醒词「你好艾拉」、断句、识别、按句合成、五个元音口型同步、插话打断、回声过滤，全部离线跑在 CPU 上
- [ ] 账号与对话同步（Spring Boot）、长期记忆、个人知识库
- [ ] 浏览器操作、工具调用、屏幕感知报错
- [ ] 魔法工作台：3D 代码星图、手势操控、代码 RAG、Agent 修复与自动回滚

## 仓库结构

```
aila-desktop/   桌面端：Electron + Vue 3 + Three.js（@pixiv/three-vrm）
aila-ai/        本地 AI 服务：Python + FastAPI，调用大模型、工具调用（搜索、读网页）、解析表情动作标签、语音（sherpa-onnx）
```

后面还会加 Spring Boot 云端服务。

## 快速开始

需要 Node.js 20+、Python 3.11+。

```powershell
# 1. 本地 AI 服务
cd aila-ai
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python scripts\download_models.py   # 语音模型，约 370MB
copy .env.example .env          # 填大模型密钥（和可选的搜索密钥）；不填就用内置假模型
.venv\Scripts\python -m app     # 监听 127.0.0.1:8765

# 2. 桌面端（另开一个终端）
cd aila-desktop
npm install
npm run dev
```

角色模型不在仓库里（体积大），下载方式见 [aila-desktop/README.md](aila-desktop/README.md)。没有模型时会显示一个简易占位角色。

## 架构

```
┌──────────────── 你的电脑 ────────────────┐
│  aila-desktop（Electron）                │
│    渲染层：3D 角色、行为、气泡、麦克风   │
│    主进程：窗口、快捷键、转发对话和合成  │
│                 │ HTTP + SSE、WebSocket  │
│  aila-ai（FastAPI，只监听本机）          │
│    对话：人设、工具调用、标签解析        │
│    语音：唤醒、断句、识别、合成          │
└──────────┼───────────────────┼───────────┘
           │ OpenAI 兼容接口   │ HTTPS（只许公网地址）
    大模型（DeepSeek 等）   搜索服务（博查/Tavily）、网页
```

各部分的设计说明见子目录的 README。
