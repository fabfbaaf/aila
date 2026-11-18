"""简单的结果缓存。"""


def memoize(fn):
    store = {}

    def wrapper(*args, **kwargs):
        if args not in store:
            store[args] = fn(*args, **kwargs)
        return store[args]

    return wrapper
