from invoice import invoice_total
from quote import quote
from receipt import receipt_line


def test_behaviour():
    assert invoice_total(100) == "113.00 CNY"
    assert receipt_line("茶", 10) == "茶: 10.00 CNY（含税 1.30）"
    assert quote(100, 0.1) == {"net": 90.0, "tax": 11.7, "currency": "CNY"}
