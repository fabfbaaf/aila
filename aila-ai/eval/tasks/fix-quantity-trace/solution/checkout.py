"""把界面输入的购买数量转为整数，再计算总价。"""

import re


def parse_quantity(raw: str) -> int:
    match = re.fullmatch(r"\s*(\d+)件?\s*", raw)
    if not match:
        raise ValueError(f"数量不对：{raw!r}")
    return int(match.group(1))


def checkout_total(price: float, raw: str) -> float:
    return price * parse_quantity(raw)
