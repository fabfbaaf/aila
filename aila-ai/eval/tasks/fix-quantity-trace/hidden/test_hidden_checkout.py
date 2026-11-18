import pytest
from checkout import parse_quantity


def test_more_valid():
    assert parse_quantity("10件") == 10
    assert parse_quantity("0") == 0


@pytest.mark.parametrize("raw", ["", "件", " ", "1 2"])
def test_more_invalid(raw):
    with pytest.raises(ValueError):
        parse_quantity(raw)
