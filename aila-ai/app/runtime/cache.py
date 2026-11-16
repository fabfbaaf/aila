"""估算前缀缓存命中。

DeepSeek 的 API 会把请求开头和之前请求相同的部分从缓存里取（按块存，官方文档说每块 64 个 token），
命中的输入便宜得多。真模型的命中数以服务端返回的 prompt_cache_hit_tokens 为准；
这里的估算给假模型和测试用：把请求（工具定义 + 消息）按发送顺序序列化，和之前的请求比最长公共前缀。
"""

import json
from typing import Any

from ..llm import Message, Usage

CHARS_PER_TOKEN = 3  # 中英混合大致的比例，只用于估算
BLOCK_TOKENS = 64
REMEMBER = 8  # 和最近几次请求比


def serialize(messages: list[Message], tools: list[dict[str, Any]] | None) -> str:
    """工具定义在模型那边会放在最前面，然后是消息"""
    return json.dumps({"tools": tools or []}, ensure_ascii=False) + json.dumps(messages, ensure_ascii=False)


def common_prefix(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


class PrefixMeter:
    def __init__(self) -> None:
        self._previous: list[str] = []

    def measure(self, messages: list[Message], tools: list[dict[str, Any]] | None, completion_chars: int = 0) -> Usage:
        text = serialize(messages, tools)
        best = max((common_prefix(text, p) for p in self._previous), default=0)
        self._previous = (self._previous + [text])[-REMEMBER:]
        prompt = -(-len(text) // CHARS_PER_TOKEN)
        hit = (best // CHARS_PER_TOKEN) // BLOCK_TOKENS * BLOCK_TOKENS
        hit = min(hit, prompt)
        return Usage(prompt, -(-completion_chars // CHARS_PER_TOKEN), hit, prompt - hit, estimated=True)


def hit_rate(usage: Usage) -> float:
    return usage.cache_hit / usage.prompt if usage.prompt else 0.0
