"""开发票。"""

TAX_RATE = 0.13
CURRENCY = "CNY"


def invoice_total(amount):
    return f"{amount * (1 + TAX_RATE):.2f} {CURRENCY}"
