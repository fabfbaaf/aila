"""库存。"""

STOCK = {"tea": 5}


def reserve_stock(items):
    for sku in items:
        STOCK[sku] -= 1
