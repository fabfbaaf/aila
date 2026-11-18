"""折扣函数的契约测试，新功能还要补上空列表和边界用例。"""

import pytest
import pricing


def test_original_total() -> None:
    assert pricing.total([10, 20]) == 30


def test_discount_contract() -> None:
    assert pricing.discounted_total([10, 30], 25) == 30


@pytest.mark.parametrize("percent", [-1, 101])
def test_discount_rejects_invalid_percent(percent: int) -> None:
    with pytest.raises(ValueError):
        pricing.discounted_total([10], percent)


def test_empty_cart() -> None:
    assert pricing.discounted_total([], 50) == 0


@pytest.mark.parametrize("percent, expected", [(0, 100), (100, 0)])
def test_edges(percent: int, expected: float) -> None:
    assert pricing.discounted_total([100], percent) == expected
