# aila-ai：艾拉的本地 AI 服务

桌面端把对话历史发过来，这里调用大模型，把回复里的表情、动作标签拆出来，用 SSE 流式推回去。
只监听 `127.0.0.1`，不对外开放。

## 运行

```powershell
cd aila-ai
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
copy .env.example .env      # 填 LLM_API_KEY；不填就用假模型
.venv\Scripts\python -m app # 监听 127.0.0.1:8765
```

测试：`.venv\Scripts\python -m pytest`

## 接口

`POST /chat`

```json
{ "messages": [{ "role": "user", "content": "你好" }] }
```

返回 `text/event-stream`，每个事件一行 JSON：

```
data: {"type": "emotion", "name": "happy"}
data: {"type": "text", "text": "你好。"}
data: {"type": "action", "name": "nod"}
data: {"type": "text", "text": "今天也请多指教。"}
data: {"type": "done"}
```

出错时最后一个事件是 `{"type": "error", "code": "auth" | "rate_limit" | "quota" | "network" | "unknown", "message": "..."}`。

`GET /health` → `{"ok": true, "model": "deepseek-chat"}`

## 设计

- **标签由服务端解析**：桌面端只收结构化事件，不需要知道提示词里标签怎么写；以后换标签格式、换模型，桌面端不用改。
- **流式解析**：标签可能被拆在两个数据块里（`[ha` + `ppy]`），解析器会缓冲；只认白名单里的标签，代码里的 `a[i]` 原样保留。
- **服务端无状态**：对话历史由桌面端带过来，这里只截取最近 `AILA_MAX_HISTORY` 条，并合并连续的同角色消息（部分模型不接受）。长期记忆在第 5 周做。
- **换模型只改 `.env`**：用 OpenAI 兼容接口，DeepSeek、通义千问、Kimi 都能直接用。
- **没有密钥也能开发**：`LLM_API_KEY` 留空时用假模型，按小块慢慢吐出带标签的固定回复，整条链路照样能调。

## 目录

```
app/
  main.py      HTTP 接口、SSE 输出、对话历史整理
  llm.py       大模型接入（OpenAI 兼容 / 假模型）和错误归类
  tags.py      流式标签解析
  persona.py   艾拉的人设提示词、表情和动作白名单
  config.py    配置
tests/         标签解析和接口测试
```
