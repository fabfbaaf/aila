from signup import validate_signup

GOOD = {"username": "aila_01", "email": "a@b.cn", "password": "abc12345", "confirm": "abc12345"}


def test_good_form():
    assert validate_signup(GOOD) == {}


def test_bad_email():
    assert set(validate_signup({**GOOD, "email": "nope"})) == {"email"}
