import pytest
from lru import LRUCache


def test_get_refreshes():
    c = LRUCache(2)
    c.put("a", 1)
    c.put("b", 2)
    assert c.get("a") == 1
    c.put("c", 3)
    assert c.get("b") is None and c.get("a") == 1


def test_put_existing_updates_and_refreshes():
    c = LRUCache(2)
    c.put("a", 1)
    c.put("b", 2)
    c.put("a", 10)
    c.put("c", 3)
    assert c.get("a") == 10 and c.get("b", "没有") == "没有"
    assert len(c) == 2


def test_capacity():
    with pytest.raises(ValueError):
        LRUCache(0)
