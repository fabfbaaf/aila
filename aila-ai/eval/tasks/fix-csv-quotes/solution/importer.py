"""从 CSV 文本导入商品。"""

import csv
import io


def load_products(text):
    rows = [row for row in csv.reader(io.StringIO(text)) if any(cell.strip() for cell in row)]
    if not rows:
        return []
    header, *body = rows
    keys = [h.strip() for h in header]
    return [dict(zip(keys, [cell.strip() for cell in row])) for row in body]
