"""创建订单，不在这里改变账单金额。"""


def create_order(amount: float) -> dict:
    return {"paid": amount, "refunded": 0}
