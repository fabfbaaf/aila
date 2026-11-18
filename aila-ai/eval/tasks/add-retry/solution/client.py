"""调用不稳定的服务。"""

import time


class TransientError(Exception):
    """暂时性的失败（网络抖动、限流），过一会儿再试可能就好了"""


def call_with_retry(fn, attempts=3, base_delay=0.5, sleep=time.sleep):
    if attempts < 1:
        raise ValueError("attempts 要大于 0")
    for i in range(attempts):
        try:
            return fn()
        except TransientError:
            if i == attempts - 1:
                raise
            sleep(base_delay * (2 ** i))
