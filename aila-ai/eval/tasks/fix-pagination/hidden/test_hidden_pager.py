import pytest
from pager import page_count, paginate


def test_last_partial_page():
    assert paginate(list(range(10)), 4, 3) == [9]
    assert paginate(list(range(10)), 5, 3) == []


def test_page_count():
    assert page_count(list(range(10)), 3) == 4
    assert page_count(list(range(9)), 3) == 3
    assert page_count([], 3) == 0


@pytest.mark.parametrize("page, size", [(0, 3), (1, 0), (-1, 2)])
def test_bad_arguments(page, size):
    with pytest.raises(ValueError):
        paginate([1, 2, 3], page, size)
