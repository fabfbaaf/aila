from client import TransientError, call_with_retry


def test_succeeds_after_two_failures():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise TransientError()
        return "ok"

    assert call_with_retry(flaky, sleep=lambda s: None) == "ok"
    assert len(calls) == 3
