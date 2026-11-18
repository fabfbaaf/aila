"""价格规则。"""


def apply_rules(item, qty):
    """单价 × 数量；数量达到 bulk_from 打九折。"""
    subtotal = item["price"] * qty
    if qty >= item["bulk_from"]:
        subtotal *= 0.9
    return round(subtotal, 2)
