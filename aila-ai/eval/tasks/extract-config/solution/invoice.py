"""开发票。"""

from settings import CURRENCY, TAX_RATE


def invoice_total(amount):
    return f"{amount * (1 + TAX_RATE):.2f} {CURRENCY}"
