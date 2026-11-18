"""报价单。"""

TAX_RATE = 0.13
CURRENCY = "CNY"


def quote(amount, discount=0):
    net = amount * (1 - discount)
    return {"net": round(net, 2), "tax": round(net * TAX_RATE, 2), "currency": CURRENCY}
