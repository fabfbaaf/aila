"""账单平分。"""

from decimal import Decimal


def split_bill(total, people):
    if people < 1:
        raise ValueError("至少要有一个人")
    cents = int((Decimal(total) * 100).to_integral_value())
    base, extra = divmod(cents, people)
    return [Decimal(base + (1 if i < extra else 0)) / 100 for i in range(people)]
