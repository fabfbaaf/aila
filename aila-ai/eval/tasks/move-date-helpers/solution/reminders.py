"""提醒。"""

import dates


def remind(text, when):
    return f"{dates.format_date(dates.parse_date(when))}：{text}"
