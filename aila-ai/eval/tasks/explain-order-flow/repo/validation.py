"""校验。"""


def validate_order(items):
    if not items:
        raise ValueError("空订单")
