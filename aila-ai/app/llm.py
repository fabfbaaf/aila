"""大模型接入：兼容 OpenAI 接口的真模型，和不联网的假模型"""

import asyncio
import json
import re
import secrets
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


@dataclass
class Reasoning:
    """思考模型的思考过程（一小段）"""

    text: str


@dataclass
class Usage:
    """一次请求的用量。cache_hit：开头和之前请求相同、命中服务端缓存的输入（比没命中的便宜得多）"""

    prompt: int = 0
    completion: int = 0
    cache_hit: int = 0
    cache_miss: int = 0
    estimated: bool = False  # 假模型按前缀估算的，不是服务端返回的

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.prompt + other.prompt,
            self.completion + other.completion,
            self.cache_hit + other.cache_hit,
            self.cache_miss + other.cache_miss,
            self.estimated or other.estimated,
        )


StepItem = str | Reasoning | ToolCall | Usage


class CoderModel(Protocol):
    """编程 Agent 用的模型：一步 = 一次请求，流式给出文字、思考、工具调用，最后给出用量"""

    name: str

    def step(
        self, messages: list[Message], tools: list[dict[str, Any]] | None, max_tokens: int = 8192
    ) -> AsyncIterator[StepItem]: ...


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
            yield ToolCall(id=s["id"] or f"call_{index}_{secrets.token_hex(4)}", name=s["name"], arguments=s["arguments"] or "{}")

    async def step(
        self, messages: list[Message], tools: list[dict[str, Any]] | None, max_tokens: int = 8192
    ) -> AsyncIterator[StepItem]:
        """编程 Agent 的一步：温度 0（写代码要稳定），输出放宽，流结束时带上用量（含缓存命中）"""
        kwargs: dict[str, Any] = {"tools": tools} if tools else {}
        response = await self._client.chat.completions.create(
            model=self.name,
            messages=messages,  # type: ignore[arg-type]
            stream=True,
            temperature=0.0,
            max_tokens=max_tokens,
            stream_options={"include_usage": True},
            timeout=180.0,
            **kwargs,
        )
        pending: dict[int, dict[str, str]] = {}
        usage: Usage | None = None
        async for chunk in response:
            if chunk.usage:
                usage = usage_of(chunk.usage)
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            thought = getattr(delta, "reasoning_content", None)
            if thought:
                yield Reasoning(thought)
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
            yield ToolCall(id=s["id"] or f"call_{index}_{secrets.token_hex(4)}", name=s["name"], arguments=s["arguments"] or "{}")
        if usage:
            yield usage

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
            text = re.sub(r"^<背景>.*?</背景>\s*", "", str(last["content"]), flags=re.S)  # 只看用户说的话
            url = re.search(r"https?://[^\s，。]+", text)
            if ("工作窗口" in text or "任务页" in text or text.startswith("编程任务")) and "coding_task" in names:  # 交给编程 Agent
                reply = "[nod]好，我去任务页里做。"
                task = re.sub(r"^编程任务[：:]?\s*", "", text)
                where = re.search(r"在\s*([A-Za-z0-9_.\-]+)\s*(?:项目)?里", task)  # 「在 demo 里……」
                args = {"task": task, **({"project": where.group(1)} if where else {})}
                call = ToolCall("mock-coding", "coding_task", json.dumps(args, ensure_ascii=False))
            elif (m := re.search(r"(\d+)\s*分钟后.*?(?:提醒|叫)我(.*)", text)) and "set_reminder" in names:
                reply = "[nod]好。"
                what = m.group(2).strip(" ，。,.") or "时间到了"
                call = ToolCall("mock-remind", "set_reminder", json.dumps({"text": what, "in_minutes": int(m.group(1))}, ensure_ascii=False))
            elif text.startswith("我这边报错了") and "code_locate" in names:  # 看屏幕看到的报错：先找出错的代码
                reply = "[think]我看一下是哪里的代码……"
                call = ToolCall("mock-locate", "code_locate", json.dumps({"error": text}, ensure_ascii=False))
            elif url and ("网页上" in text or "你的浏览器" in text) and "browser_open" in names:
                reply = "[nod]好，我去看看。"
                call = ToolCall("mock-browse", "browser_open", json.dumps({"url": url.group(0)}))
            elif url and "打开" in text and "open_url" in names:
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
        if line.startswith(("读取失败", "网络出错", "请求失败", "打不开", "网页打不开")):
            return f"[sad]……没读成。{line}"
        if line.startswith("在用户的资料里没有找到"):
            return "[shake]……资料里没有找到相关的内容。"
        if line.startswith("第 1 处："):
            return f"[nod]找到了，出错的地方{line[5:]}。[relaxed]……不过我现在是假模型，分析不了原因。"
        if line.startswith("调用栈里的位置都不在登记的项目里") or line.startswith("这段报错里没认出调用栈"):
            return "[tilt]……这个报错不在你登记的项目里，我找不到对应的代码。"
        if line.startswith("已经在任务页里开始做了"):
            return "[nod]在任务页里做着了……[relaxed]做完告诉你。"
        if line.startswith("记好了："):
            return f"{line[4:].split('提醒用户')[0].strip()} 叫你。"
        if line.startswith("有好几个项目"):
            return "[tilt]……是哪个项目呢？"
        if line.startswith("还不知道用户的项目在哪"):
            return "[tilt]……我还不知道你的项目在哪。在主窗口的任务页点「新任务」选一个文件夹吧。"
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
    if text.startswith("我这边报错了"):  # 看屏幕看到的报错，用户同意让她看看
        return "[think]我看到了……[sad]不过我现在是假模型，还分析不了原因。[nod]填好大模型密钥，我就能帮你看了。"
    if any(w in text for w in ("报错", "bug", "错误", "Error")):
        return "[surprised]诶……出错了吗？[think]把报错贴给我，或者打开「留意屏幕上的报错」，[nod]我帮你看看。"
    return "[think]……现在还没有接上大模型。[tilt]在设置的「对话」里填好大模型密钥，[nod]我就能好好回答你了。"


def usage_of(raw: Any) -> Usage:
    """服务端返回的用量 → Usage。DeepSeek 用 prompt_cache_hit_tokens / prompt_cache_miss_tokens，
    OpenAI 风格的服务用 prompt_tokens_details.cached_tokens"""
    prompt = int(getattr(raw, "prompt_tokens", 0) or 0)
    completion = int(getattr(raw, "completion_tokens", 0) or 0)
    extra = getattr(raw, "model_extra", None) or {}
    hit = getattr(raw, "prompt_cache_hit_tokens", None) or extra.get("prompt_cache_hit_tokens")
    miss = getattr(raw, "prompt_cache_miss_tokens", None) or extra.get("prompt_cache_miss_tokens")
    if hit is None:
        details = getattr(raw, "prompt_tokens_details", None)
        hit = getattr(details, "cached_tokens", 0) if details else 0
    hit = int(hit or 0)
    miss = int(miss) if miss is not None else max(0, prompt - hit)
    return Usage(prompt, completion, hit, miss)


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
