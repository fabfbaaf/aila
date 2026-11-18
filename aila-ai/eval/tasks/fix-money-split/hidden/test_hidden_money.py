from decimal import Decimal

import pytest
from money import split_bill


def test_small_amount():
    shares = split_bill(Decimal("0.05"), 3)
    assert shares == [Decimal("0.02"), Decimal("0.02"), Decimal("0.01")]
    assert sum(shares) == Decimal("0.05")


def test_one_person_and_even():
    assert split_bill(Decimal("10"), 1) == [Decimal("10")]
    assert split_bill(Decimal("9.99"), 3) == [Decimal("3.33")] * 3


def test_no_people():
    with pytest.raises(ValueError):
        split_bill(Decimal("10"), 0)
