from tags import add_tag


def test_appends_to_given_list():
    mine = ["a"]
    assert add_tag("b", mine) is mine
    assert mine == ["a", "b"]


def test_no_duplicates():
    assert add_tag("a", ["a"]) == ["a"]
