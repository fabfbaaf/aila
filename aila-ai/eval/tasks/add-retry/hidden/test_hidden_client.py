import pytest
from client import TransientError, call_with_retry


def test_backoff_delays():
    waits = []

    def always():
        raise TransientError("还是不行")

    with pytest.raises(TransientError, match="还是不行"):
        call_with_retry(always, attempts=4, base_delay=0.5, sleep=waits.append)
    assert waits == [0.5, 1.0, 2.0]


def test_other_errors_are_not_retried():
    calls = []

    def broken():
        calls.append(1)
        raise KeyError("x")

    with pytest.raises(KeyError):
        call_with_retry(broken, sleep=lambda s: None)
    assert len(calls) == 1


def test_attempts_must_be_positive():
    with pytest.raises(ValueError):
        call_with_retry(lambda: 1, attempts=0)
