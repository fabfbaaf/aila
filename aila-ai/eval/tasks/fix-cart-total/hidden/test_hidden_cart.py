from cart import total


def test_more_cases():
    assert total([], shipping=3) == 3
    assert total([1.5, 2.5], shipping=1) == 5
