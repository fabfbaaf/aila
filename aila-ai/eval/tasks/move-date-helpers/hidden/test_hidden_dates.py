from datetime import date
from pathlib import Path

import dates
import utils


def test_moved():
    assert dates.parse_date("2026-01-02") == date(2026, 1, 2)
    assert dates.format_date(date(2026, 1, 2)) == "2026 年 1 月 2 日"
    assert not hasattr(utils, "parse_date") and not hasattr(utils, "format_date")
    assert utils.clamp(5, 0, 3) == 3


def test_callers_import_from_dates():
    for name in ("events.py", "reminders.py"):
        text = Path(name).read_text(encoding="utf-8")
        assert "utils.parse_date" not in text and "utils.format_date" not in text, name
