"""去掉重复元素，保持第一次出现的顺序。"""


def unique(items):
    return list(dict.fromkeys(items))
