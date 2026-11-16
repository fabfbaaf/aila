"""带单位、空格和非法输入的数量转换。"""

import pytest
from checkout import checkout_total, parse_quantity


def test_quantity_with_unit() -> None:
    assert checkout_total(12, " 2件 ") == 24


def test_plain_quantity() -> None:
    assert parse_quantity(" 3 ") == 3


@pytest.mark.parametrize("raw", ["-1", "2件件", "2x", "2件3"])
def test_bad_quantity(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_quantity(raw)
