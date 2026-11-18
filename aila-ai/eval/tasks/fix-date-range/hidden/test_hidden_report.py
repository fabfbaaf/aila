from datetime import date

from report import orders_between

ORDERS = [{"id": i, "day": date(2026, 1, i)} for i in range(1, 6)]


def test_single_day():
    assert [o["id"] for o in orders_between(ORDERS, date(2026, 1, 3), date(2026, 1, 3))] == [3]


def test_reversed_range_is_empty():
    assert orders_between(ORDERS, date(2026, 1, 4), date(2026, 1, 2)) == []
