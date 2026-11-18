"""按日期筛订单。"""


def in_range(day, start, end):
    return start <= day < end


def orders_between(orders, start, end):
    return [o for o in orders if in_range(o["day"], start, end)]
