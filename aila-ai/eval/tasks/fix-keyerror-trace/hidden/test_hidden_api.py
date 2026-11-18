from api import quote


def test_bulk_and_other_item():
    assert quote("tea", 10)["total"] == 108
    assert quote("cup", 1)["total"] == 30
