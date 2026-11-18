from pathlib import Path


def test_old_name_gone_everywhere():
    for path in Path(".").glob("*.py"):
        if path.name.startswith("test_"):
            continue
        assert "lookup_product" not in path.read_text(encoding="utf-8"), path.name
