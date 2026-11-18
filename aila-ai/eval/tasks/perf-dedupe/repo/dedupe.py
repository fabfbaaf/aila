"""去掉重复元素，保持第一次出现的顺序。"""


def unique(items):
    seen = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen
