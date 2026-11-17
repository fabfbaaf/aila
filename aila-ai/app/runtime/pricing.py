"""按模型和 token 用量算人民币费用。价格未知就保留未知，不把它当成免费调用。"""

import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

# 2026-11-16 核对 DeepSeek 官方价格页（元 / 百万 token）：
# https://api-docs.deepseek.com/zh-cn/quick_start/pricing/
# 官网分高峰和空闲两档（空闲半价；高峰是北京时间工作日 9–12 点、14–18 点）。这里只能记一个价，
# 记高峰价：算出来的钱只会偏多，上限宁可早停也不能晚停。按空闲价算的话在 data/prices.json 覆盖。
# 价格页注明旧名 deepseek-v4-flash 现在由 V4.1 Flash 服务、按 Flash 计价。
# deepseek-chat、deepseek-reasoner 官方更新记录（https://api-docs.deepseek.com/updates/）公告
# 2026-07-24 停用，之后的状态没写，所以不估价；还在用的话换成 deepseek-flash。
_FLASH = {"hit": 0.04, "miss": 2, "output": 8}
DEFAULT_PRICES: dict[str, dict[str, float] | None] = {
    "deepseek-flash": _FLASH,
    "deepseek-v4-flash": _FLASH,
    "deepseek-v4-pro": {"hit": 0.30, "miss": 9.0, "output": 27.0},
    "deepseek-chat": None,
    "deepseek-reasoner": None,
}
MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class Price:
    hit: Decimal
    miss: Decimal
    output: Decimal


def _price(value: Any) -> Price | None:
    if not isinstance(value, dict):
        return None
    rates: list[Decimal] = []
    for field in ("hit", "miss", "output"):
        raw = value.get(field)
        if isinstance(raw, bool) or not isinstance(raw, (int, float, str, Decimal)):
            return None
        try:
            number = Decimal(str(raw))
            converted = float(number)
            if not number.is_finite() or number < 0 or not math.isfinite(converted) or (number != 0 and converted == 0):
                return None
        except (InvalidOperation, OverflowError, ValueError):
            return None
        rates.append(number)
    return Price(*rates)


def validate_budget(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError("花费上限要是有限的非负数，或 null（不限）。")
    try:
        amount = float(value)
    except (OverflowError, ValueError) as err:
        raise ValueError("花费上限要是有限的非负数，或 null（不限）。") from err
    if not math.isfinite(amount) or amount < 0:
        raise ValueError("花费上限要是有限的非负数，或 null（不限）。")
    return amount


def _amount(event: Mapping[str, Any], prices: Mapping[str, Price | None]) -> Decimal | None:
    model = event.get("model")
    price = prices.get(model) if isinstance(model, str) else None
    if price is None:
        return None
    counts = [event.get(field) for field in ("cache_hit", "cache_miss", "completion")]
    if any(type(count) is not int or count < 0 for count in counts):
        return None
    return (counts[0] * price.hit + counts[1] * price.miss + counts[2] * price.output) / MILLION


def _finite_float(amount: Decimal) -> float | None:
    try:
        value = float(amount)
    except OverflowError:
        return None
    return value if math.isfinite(value) else None


class PriceTable:
    """每次汇总时读取覆盖文件，修改价格后已加载的会话也能立即重算。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path

    def _prices(self) -> dict[str, Price | None]:
        prices = {model: _price(value) for model, value in DEFAULT_PRICES.items()}
        if self.path is None:
            return prices
        try:
            overrides = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return prices
        if not isinstance(overrides, dict):
            return prices
        for model, value in overrides.items():
            # 非法价格与缺少价格都按未知处理，不能用 false、NaN 等数据生成零价。
            prices[model] = _price(value)
        return prices

    def amount(self, event: Mapping[str, Any]) -> Decimal | None:
        return _amount(event, self._prices())

    def cost(self, event: Mapping[str, Any]) -> float | None:
        amount = self.amount(event)
        return None if amount is None else _finite_float(amount)

    def totals(self, events: Iterable[Mapping[str, Any]], model: str = "") -> tuple[float | None, Decimal]:
        prices = self._prices()
        known = Decimal(0)
        complete = True
        used = False
        for event in events:
            if event.get("type") != "usage":
                continue
            used = True
            amount = _amount(event, prices)
            if amount is None:
                complete = False
            else:
                known += amount
        if not used:
            complete = prices.get(model) is not None
        # 混有未知模型时总体仍未知。已知小计用于上限下界判断，避免未知用量清空已有花费。
        return (_finite_float(known) if complete else None), known


def cost(usage_event: Mapping[str, Any], prices: PriceTable | None = None) -> float | None:
    return (prices or PriceTable()).cost(usage_event)
