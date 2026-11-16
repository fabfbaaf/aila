"""检查新接口，以及两个调用方仍正常工作。"""

import catalog
from app import product_name
from orders import order_total


def test_new_name_replaces_old_name() -> None:
    assert hasattr(catalog, "find_product")
    assert not hasattr(catalog, "lookup_product")


def test_callers() -> None:
    assert product_name("tea") == "茶"
    assert order_total("tea", 2) == 24
