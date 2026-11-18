"""商品表和按编号查商品的接口。"""

PRODUCTS = {"tea": {"name": "茶", "price": 12}}


def find_product(sku: str) -> dict:
    return PRODUCTS[sku]
