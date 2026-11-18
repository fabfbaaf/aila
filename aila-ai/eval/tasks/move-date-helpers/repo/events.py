"""活动报名。"""

from utils import clamp, format_date, parse_date


def event_title(name, when):
    return f"{name}（{format_date(parse_date(when))}）"


def seats_left(total, taken):
    return clamp(total - taken, 0, total)
