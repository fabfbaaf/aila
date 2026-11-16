"""下单时查询商品价格。"""

from catalog import lookup_product


def order_total(sku: str, quantity: int) -> float:
    return lookup_product(sku)["price"] * quantity
