"""简单的结果缓存。"""

import functools


def memoize(fn):
    store = {}

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        key = (args, tuple(sorted(kwargs.items())))
        if key not in store:
            store[key] = fn(*args, **kwargs)
        return store[key]

    return wrapper
