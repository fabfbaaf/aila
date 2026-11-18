from tags import add_tag


def test_separate_calls_do_not_share():
    assert add_tag("python") == ["python"]
    assert add_tag("rust") == ["rust"]
