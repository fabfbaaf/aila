"""日期的解析和显示。"""

from datetime import datetime


def parse_date(text):
    return datetime.strptime(text.strip(), "%Y-%m-%d").date()


def format_date(day):
    return f"{day.year} 年 {day.month} 月 {day.day} 日"
