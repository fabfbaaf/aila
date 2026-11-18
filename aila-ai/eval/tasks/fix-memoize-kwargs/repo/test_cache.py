from cache import memoize


@memoize
def price(base, scale=1):
    return base * scale


def test_kwargs_matter():
    assert price(1, scale=2) == 2
    assert price(1, scale=3) == 3
