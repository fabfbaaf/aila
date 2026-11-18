from cache import memoize

calls = []


@memoize
def area(w=1, h=1):
    """面积"""
    calls.append((w, h))
    return w * h


def test_cached_once_regardless_of_order():
    assert area(w=2, h=3) == 6
    assert area(h=3, w=2) == 6
    assert calls == [(2, 3)]


def test_keeps_metadata():
    assert area.__name__ == "area" and area.__doc__ == "面积"
