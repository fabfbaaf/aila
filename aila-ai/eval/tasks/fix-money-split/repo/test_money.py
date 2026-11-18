from decimal import Decimal

from money import split_bill


def test_three_people():
    assert split_bill(Decimal("100"), 3) == [Decimal("33.34"), Decimal("33.33"), Decimal("33.33")]
