import re
from pathlib import Path

import settings


def test_defined_once():
    assert settings.TAX_RATE == 0.13 and settings.CURRENCY == "CNY"
    for name in ("invoice.py", "receipt.py", "quote.py"):
        text = Path(name).read_text(encoding="utf-8")
        assert not re.search(r"^\s*(TAX_RATE|CURRENCY)\s*=", text, re.M), name

