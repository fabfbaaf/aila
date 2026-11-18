"""打小票。"""

TAX_RATE = 0.13
CURRENCY = "CNY"


def receipt_line(name, amount):
    return f"{name}: {amount:.2f} {CURRENCY}（含税 {amount * TAX_RATE:.2f}）"
