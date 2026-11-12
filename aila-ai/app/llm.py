"""大模型接入：兼容 OpenAI 接口的真模型，和不联网的假模型"""

import asyncio
from collections.abc import AsyncIterator
from typing import Protocol

import openai
from openai import AsyncOpenAI

from .config import Settings


class ChatModel(Protocol):
    name: str

    def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        """流式返回回复文本（可能夹着表情和动作标签）"""
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

    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        response = await self._client.chat.completions.create(
            model=self.name,
            messages=messages,  # type: ignore[arg-type]
            stream=True,
            temperature=0.8,
            max_tokens=500,
        )
        async for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


class MockModel:
    """没配密钥时用：几句固定回复，按小块慢慢吐出来，模拟真实的流式输出"""

    name = "mock"

    def __init__(self, delay: float = 0.04) -> None:
        self._delay = delay

    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        reply = _mock_reply(messages[-1]["content"])
        # 每 3 个字一块，故意把标签拆开，和真模型一样
        for i in range(0, len(reply), 3):
            await asyncio.sleep(self._delay)
            yield reply[i : i + 3]


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
