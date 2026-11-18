from datetime import date

from report import orders_between

ORDERS = [{"id": 1, "day": date(2026, 1, 1)}, {"id": 2, "day": date(2026, 1, 31)}]


def test_end_day_included():
    assert [o["id"] for o in orders_between(ORDERS, date(2026, 1, 1), date(2026, 1, 31))] == [1, 2]
