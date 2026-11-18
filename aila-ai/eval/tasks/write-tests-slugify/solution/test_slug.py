from slug import slugify


def test_lowercase():
    assert slugify("Hello") == "hello"


def test_separators_collapse():
    assert slugify("hello   world") == "hello-world"
    assert slugify("a, b_c") == "a-b-c"


def test_trim():
    assert slugify("!Hello!") == "hello"


def test_chinese_kept():
    assert slugify("你好 世界") == "你好-世界"


def test_empty():
    assert slugify("") == ""
