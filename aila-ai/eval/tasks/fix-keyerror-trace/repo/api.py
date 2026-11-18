"""对外的报价接口。"""

import service


def quote(sku, qty):
    if qty < 1:
        raise ValueError("数量要大于 0")
    # 报价 = 单价 × 数量，量大打九折
    return {"sku": sku, "total": service.line_total(sku, qty)}
