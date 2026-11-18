from events import event_title, seats_left
from reminders import remind


def test_behaviour():
    assert event_title("读书会", "2026-03-08") == "读书会（2026 年 3 月 8 日）"
    assert seats_left(10, 12) == 0
    assert remind("交房租", " 2026-04-01 ") == "2026 年 4 月 1 日：交房租"
