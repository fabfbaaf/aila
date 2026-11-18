"""杂七杂八的小工具。"""

from datetime import date, datetime


def parse_date(text):
    return datetime.strptime(text.strip(), "%Y-%m-%d").date()


def format_date(day):
    return f"{day.year} 年 {day.month} 月 {day.day} 日"


def clamp(value, low, high):
    return max(low, min(high, value))
