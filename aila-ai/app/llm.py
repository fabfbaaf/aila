"""大模型接入：兼容 OpenAI 接口的真模型，和不联网的假模型"""

import asyncio
import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol

import openai
from openai import AsyncOpenAI

from .config import Settings

Message = dict[str, Any]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON 字符串，原样来自大模型，可能不合法


class ChatModel(Protocol):
    name: str

    def stream(self, messages: list[Message], tools: list[dict[str, Any]] | None = None) -> AsyncIterator[str | ToolCall]:
        """流式返回回复文本（可能夹着表情和动作标签）；要调用工具时，最后给出 ToolCall"""
        ...


class OpenAICompatibleModel:
    """DeepSeek、通义千问、Kimi 等都兼容这个接口，换模型只改 .env"""

    def __init__(self, settings: Settings) -> None:
        self.name = settings.llm_model
        self._client = AsyncOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            timeout=60.0,
            max_retries=1,
        )

    async def stream(self, messages: list[Message], tools: list[dict[str, Any]] | None = None) -> AsyncIterator[str | ToolCall]:
        kwargs: dict[str, Any] = {}
        if tools:
            kwargs["tools"] = tools
        response = await self._client.chat.completions.create(
            model=self.name,
            messages=messages,  # type: ignore[arg-type]
            stream=True,
            temperature=0.8,
            max_tokens=800,
            **kwargs,
        )
        # 工具调用在流里是分片来的：同一个 index 的参数要拼起来，流结束后才完整
        pending: dict[int, dict[str, str]] = {}
        async for chunk in response:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content
            for tc in delta.tool_calls or []:
                slot = pending.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function and tc.function.name and not slot["name"]:
                    slot["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    slot["arguments"] += tc.function.arguments
        for index in sorted(pending):
            s = pending[index]
            yield ToolCall(id=s["id"] or f"call_{index}", name=s["name"], arguments=s["arguments"] or "{}")

    async def ping(self) -> None:
        """设置界面的「测试连接」：发一个最短的请求，看密钥、地址、模型名对不对"""
        await self._client.chat.completions.create(
            model=self.name, messages=[{"role": "user", "content": "你好"}], max_tokens=1
        )

    async def complete(self, messages: list[Message]) -> str:
        """一次性拿到完整回答（整理记忆这类后台任务用）：温度低一点，要求输出 JSON"""
        response = await self._client.chat.completions.create(
            model=self.name,
            messages=messages,  # type: ignore[arg-type]
            temperature=0.2,
            max_tokens=600,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content or ""


class MockModel:
    """没配密钥时用：几句固定回复，按小块慢慢吐出来，模拟真实的流式输出"""

    name = "mock"

    def __init__(self, delay: float = 0.04) -> None:
        self._delay = delay

    async def stream(self, messages: list[Message], tools: list[dict[str, Any]] | None = None) -> AsyncIterator[str | ToolCall]:
        last = messages[-1]
        names = {t["function"]["name"] for t in tools or []}
        call: ToolCall | None = None

        if last["role"] == "tool":
            reply = _mock_summary(str(last["content"]))
        else:
            text = str(last["content"])
            url = re.search(r"https?://[^\s，。]+", text)
            if url and "打开" in text and "open_url" in names:
                reply = "[nod]好，我帮你打开。"
                call = ToolCall("mock-open", "open_url", json.dumps({"url": url.group(0)}))
            elif url and "read_page" in names:
                reply = "[nod]好，我看一下。"
                call = ToolCall("mock-read", "read_page", json.dumps({"url": url.group(0)}))
            elif any(w in text for w in ("资料", "文档", "笔记", "文件里")) and "search_knowledge" in names:
                reply = "[think]我翻一下资料……"
                call = ToolCall("mock-kb", "search_knowledge", json.dumps({"query": text}, ensure_ascii=False))
            elif any(w in text for w in ("搜", "查一下", "search")) and "web_search" in names:
                reply = "[think]我查一下……"
                query = re.sub(r"^(帮我|请)?(搜一下|搜索|搜|查一下)", "", text).strip() or text
                call = ToolCall("mock-search", "web_search", json.dumps({"query": query}, ensure_ascii=False))
            else:
                reply = _mock_reply(text)

        # 每 3 个字一块，故意把标签拆开，和真模型一样
        for i in range(0, len(reply), 3):
            await asyncio.sleep(self._delay)
            yield reply[i : i + 3]
        if call:
            yield call


def _mock_summary(tool_result: str) -> str:
    """假模型不会真的总结，只把工具结果的第一条有用信息念出来"""
    for line in tool_result.splitlines():
        line = line.strip()
        if line.startswith("1. "):
            return f"[nod]查到了。第一条是「{line[3:]}」。[relaxed]……我现在是假模型，只能念标题给你听。"
        if line.startswith("网页标题："):
            return f"[nod]读完了，标题是「{line[5:]}」。[relaxed]……我现在是假模型，还不会总结。"
        if line.startswith("读取失败") or line.startswith("网络出错") or line.startswith("请求失败"):
            return f"[sad]……没读成。{line}"
        if line.startswith("在用户的资料里没有找到"):
            return "[shake]……资料里没有找到相关的内容。"
        if line.startswith("已经在用户的浏览器里打开了"):
            return "[nod]打开了，你看一下。"
        if line.startswith("用户没有同意"):
            return "[nod]……好，那就不打开了。"
        if line.startswith("用户一直没有回应"):
            return "[tilt]……你好像在忙，那我先不打开了。"
    return "[tilt]……工具返回了内容，但我现在是假模型，看不懂。"


def _mock_reply(text: str) -> str:
    if any(w in text for w in ("你好", "早", "晚上好", "hi", "hello")):
        return "[happy]你好。……我是艾拉。[nod]今天也请多指教。"
    if any(w in text for w in ("谢谢", "辛苦")):
        return "[relaxed]……不用客气。[tilt]能帮上忙就好。"
    if any(w in text for w in ("报错", "bug", "错误", "Error")):
        return "[surprised]诶……出错了吗？[think]现在的我还看不到代码，[sad]对不起。"
    return "[think]……现在还没有接上大模型。[tilt]在 aila-ai/.env 里填好密钥，[nod]我就能好好回答你了。"


def classify_error(err: Exception) -> tuple[str, str]:
    """把大模型调用的异常归成几类，桌面端按类别让艾拉说不同的话"""
    if isinstance(err, openai.AuthenticationError):
        return "auth", "大模型密钥无效"
    if isinstance(err, openai.RateLimitError):
        return "rate_limit", "请求太频繁"
    if isinstance(err, openai.APIStatusError) and err.status_code == 402:
        return "quota", "大模型账户余额不足"
    if isinstance(err, (openai.APIConnectionError, openai.APITimeoutError)):
        return "network", "连不上大模型服务"
    return "unknown", "大模型调用失败"
