"""列表分页。"""


def paginate(items, page, size):
    if page < 1 or size < 1:
        raise ValueError("page 和 size 要大于 0")
    start = (page - 1) * size
    return list(items)[start:start + size]


def page_count(items, size):
    if size < 1:
        raise ValueError("size 要大于 0")
    return -(-len(items) // size)
