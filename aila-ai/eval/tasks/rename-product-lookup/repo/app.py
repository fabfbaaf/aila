"""商品页面直接使用目录查询。"""

import catalog


def product_name(sku: str) -> str:
    return catalog.lookup_product(sku)["name"]
