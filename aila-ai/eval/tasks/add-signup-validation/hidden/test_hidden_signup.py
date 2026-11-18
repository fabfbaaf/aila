import pytest
from signup import validate_signup

GOOD = {"username": "aila_01", "email": "a@b.cn", "password": "abc12345", "confirm": "abc12345"}


@pytest.mark.parametrize("field, value", [
    ("username", "ab"), ("username", "a" * 21), ("username", "有中文"), ("username", "a-b-c"),
    ("email", "a@@b.cn"), ("email", "a@bcn"), ("email", "@b.cn"),
    ("password", "short1"), ("password", "abcdefgh"), ("password", "12345678"),
])
def test_each_rule(field, value):
    form = {**GOOD, field: value}
    if field == "password":
        form["confirm"] = value
    assert field in validate_signup(form)


def test_confirm_mismatch_and_missing():
    assert set(validate_signup({**GOOD, "confirm": "other123"})) == {"confirm"}
    assert {"username", "email", "password"} <= set(validate_signup({}))
