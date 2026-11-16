"""账单变化集中在这里处理，退款不能超过剩余已付金额。"""


def process_refund(order: dict, amount: float) -> dict:
    if amount < 0 or amount > order["paid"] - order["refunded"]:
        raise ValueError("退款金额不对。")
    return {**order, "refunded": order["refunded"] + amount}
