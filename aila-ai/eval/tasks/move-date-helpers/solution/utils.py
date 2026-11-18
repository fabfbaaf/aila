"""杂七杂八的小工具。"""


def clamp(value, low, high):
    return max(low, min(high, value))
