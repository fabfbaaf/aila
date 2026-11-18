"""价格计算。"""


def total(items: list[float]) -> float:
    return sum(items)


def discounted_total(items: list[float], percent: float) -> float:
    if not 0 <= percent <= 100:
        raise ValueError("percent 要在 0 到 100 之间")
    return total(items) * (100 - percent) / 100
