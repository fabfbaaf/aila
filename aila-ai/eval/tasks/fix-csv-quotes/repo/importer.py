"""从 CSV 文本导入商品。"""


def parse_line(line):
    return [cell.strip() for cell in line.split(",")]


def load_products(text):
    rows = [parse_line(line) for line in text.splitlines() if line.strip()]
    header, *body = rows
    return [dict(zip(header, row)) for row in body]
