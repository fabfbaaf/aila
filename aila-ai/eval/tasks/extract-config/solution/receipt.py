"""打小票。"""

from settings import CURRENCY, TAX_RATE


def receipt_line(name, amount):
    return f"{name}: {amount:.2f} {CURRENCY}（含税 {amount * TAX_RATE:.2f}）"
