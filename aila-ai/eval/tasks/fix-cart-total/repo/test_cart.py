"""运费和免费配送都要算对。"""

from cart import total


def test_total_with_shipping() -> None:
    assert total([10, 20], shipping=5) == 35


def test_free_shipping() -> None:
    assert total([10, 20]) == 30
