from dedupe import unique


def test_keeps_first_order():
    assert unique(["b", "a", "b", "c", "a"]) == ["b", "a", "c"]
    assert unique([]) == []
    assert unique((3, 1, 3)) == [3, 1]
