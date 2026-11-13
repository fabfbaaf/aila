"""工具确认：会改东西的操作执行前，先问用户。

流程（都在同一次 /chat 的流式回复里）：
1. 对话循环遇到要确认的工具调用，open() 登记一个待确认项，推送 confirm 事件给桌面端
2. 桌面端显示「好 / 不要」，用户回答后 POST /chat/confirm {id, approved}
3. wait() 拿到结果（或者超时），对话循环据此执行或放弃，再推送 confirm_end

id 是随机的，只出现在这次回复的事件流里，别人猜不到；/chat/confirm 和其他接口一样只接受本机页面的请求。
桌面端取消回复（用户打断、换了话题）时，流被关闭，待确认项随之作废，操作不会执行。
"""

import asyncio
import secrets
from dataclasses import dataclass, field
from typing import Literal

Result = Literal["approved", "rejected", "timeout"]

# 用户多久没回答就当没同意
CONFIRM_TIMEOUT = 120.0


@dataclass
class Pending:
    id: str
    future: asyncio.Future[bool] = field(repr=False)


class Confirmations:
    def __init__(self, timeout: float = CONFIRM_TIMEOUT) -> None:
        self.timeout = timeout
        self._pending: dict[str, Pending] = {}

    def open(self) -> Pending:
        pending = Pending(secrets.token_urlsafe(16), asyncio.get_running_loop().create_future())
        self._pending[pending.id] = pending
        return pending

    async def wait(self, pending: Pending) -> Result:
        try:
            approved = await asyncio.wait_for(asyncio.shield(pending.future), self.timeout)
        except TimeoutError:
            return "timeout"
        finally:
            self.close(pending)
        return "approved" if approved else "rejected"

    def close(self, pending: Pending) -> None:
        """不再等这个确认了（拿到结果、超时，或者回复被取消）"""
        self._pending.pop(pending.id, None)
        if not pending.future.done():
            pending.future.cancel()

    def answer(self, confirm_id: str, approved: bool) -> bool:
        """用户的回答。返回 False 表示这个确认已经结束（超时、回复被取消）或者根本不存在"""
        pending = self._pending.get(confirm_id)
        if pending is None or pending.future.done():
            return False
        pending.future.set_result(approved)
        return True

    @property
    def waiting(self) -> int:
        return len(self._pending)
