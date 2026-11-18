import time

from dedupe import unique


def test_fast_enough():
    data = list(range(20000)) * 2
    started = time.perf_counter()
    assert unique(data) == list(range(20000))
    assert time.perf_counter() - started < 0.5
