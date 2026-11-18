from api import quote


def test_quote():
    assert quote("tea", 2) == {"sku": "tea", "total": 24}
