"""提醒。"""

import utils


def remind(text, when):
    return f"{utils.format_date(utils.parse_date(when))}：{text}"
