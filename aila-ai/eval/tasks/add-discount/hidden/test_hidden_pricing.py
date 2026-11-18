import pytest
import pricing


def test_edges_and_empty():
    assert pricing.discounted_total([], 50) == 0
    assert pricing.discounted_total([100], 0) == 100
    assert pricing.discounted_total([100], 100) == 0
    assert pricing.total([1, 2]) == 3


def test_just_outside():
    with pytest.raises(ValueError):
        pricing.discounted_total([1], 100.5)
