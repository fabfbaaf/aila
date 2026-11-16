"""把界面输入的购买数量转为整数，再计算总价。"""


def parse_quantity(raw: str) -> int:
    return int(raw.strip())


def checkout_total(price: float, raw: str) -> float:
    return price * parse_quantity(raw)
