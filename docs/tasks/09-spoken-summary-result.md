# 09 对话口播摘要

分支 `codex/09-spoken-summary` 从 `main` 的 `d8279c9` 开始。

## 行为

- AI 服务从流式回复里识别开头的 `[say]…[/say]`。查资料时，工具返回后新一段开头的首个 `[say]` 也能标记结论。两个标签可跨多个数据块，事件里的回复文字保留原顺序，带 `spoken` 区分摘要和正文。
- 没有 `[say]` 时，按开头两句、最多 60 个可念字符兜底。遇到列表、代码块或网址提前收住。兜底还有正文时，整次回复末尾发一次 `hint: "more"`。空白尾部不会单独触发提示。
- 气泡显示摘要，有正文时可以点「看全文」在原处展开、滚动和选择文字。展开后气泡保留，收起后恢复显示摘要。关闭出声时也提供同样的气泡操作。
- 正常结束的聊天回复按原顺序保存全文，供「对话」页、历史同步和后续聊天使用。旧服务的文字事件没有 `spoken` 时仍然全念。兜底的「细节在对话框里」由桌面端加在摘要后，只进气泡和语音队列。

## 变更文件

| 文件 | 改动 |
|---|---|
| `aila-ai/app/tags.py` | 解析口播标签，按纯函数和流式状态判定兜底边界，保留代码方括号 |
| `aila-ai/app/agent.py` | 区分模型工具轮结束和整次聊天回复结束，避免中途产生正文提示 |
| `aila-ai/tests/test_tags.py` | 任意拆块、标签、保留原文、字数和 Markdown 边界 |
| `aila-ai/tests/test_agent.py` | 在查资料的工具轮之后识别口播结论，检查跨工具轮的兜底提示只发一次 |
| `aila-ai/tests/test_chat.py` | 验证对话 SSE 文本事件的 `spoken` 字段及断流错误顺序 |
| `aila-desktop/src/shared/chat.ts` | 对话文字事件类型增加可选的 `spoken` 和 `hint` |
| `aila-desktop/src/renderer/src/chat/Conversation.ts` | 分开处理全文记录、语音和气泡文字，保留打断、确认和进度流程 |
| `aila-desktop/src/renderer/src/chat/Conversation.test.ts` | 验证只合成摘要、静音气泡、固定提示、旧协议和打断 |
| `aila-desktop/src/renderer/src/persona/lines.ts` | 增加一句本地的全文提示 |
| `aila-desktop/src/renderer/src/components/SpeechBubble.vue` | 在现有气泡上提供可聚焦的全文开关及滚动区 |
| `aila-desktop/src/renderer/src/App.vue` | 将全文状态连到当前气泡，展开期间暂停自动收起 |
| `aila-ai/README.md` | 更新对话协议、格式和兜底说明 |
| `aila-desktop/README.md` | 说明口播、气泡展开、旧服务兼容和聊天保存时机 |
| `docs/real-test-plan.md` | 第 3.8 项补上查资料后听摘要和查阅全文的手动验收 |
| `docs/tasks/09-spoken-summary-result.md` | 本结果说明 |

## 测试

| 命令 | 结果 |
|---|---|
| `.venv\Scripts\python -X utf8 -m pytest -q -p no:cacheprovider` | 486 项通过，10 项既有跳过 |
| `.venv\Scripts\python -X utf8 -m pytest -q -p no:cacheprovider tests/test_tags.py tests/test_agent.py tests/test_chat.py` | 42 项通过 |
| `npm.cmd run typecheck` | 通过 |
| `npx.cmd vitest run` | 248 项通过，1 项既有跳过 |
| `npm.cmd run build` | 主进程、预加载、渲染层均通过 |
| `.\mvnw.cmd test` | 26 项通过，无失败、错误或跳过 |
| `git diff --check` | 通过 |

Python 测试将 `AILA_ENV_FILE` 指到不存在的测试路径，只在进程中为既有测试设置 `LLM_MODEL=deepseek-chat`，本机请求绕过代理。Python 既有的 10 项跳过是向量和语音模型资源用例。Vitest 的既有云同步端到端用例在未配置专用服务器时跳过。

## 验证边界

- 用户打断和服务报错保留原来的历史规则，只存气泡已经呈现的回复片段。服务端已发 `done` 但桌面仍在播报时打断，也按这条规则处理。
- 逐块解析、合成请求内容、静音气泡与历史分流已用假模型或假音频验证。真实模型的口播标记、实际扬声器效果和气泡里的点击、滚动观感留给第 3.8 项手动检查。人格提示词由单独任务接入。
