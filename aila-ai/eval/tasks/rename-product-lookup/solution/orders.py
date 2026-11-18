"""下单时查询商品价格。"""

from catalog import find_product


def order_total(sku: str, quantity: int) -> float:
    return find_product(sku)["price"] * quantity
