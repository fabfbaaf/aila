"""查商品、算价格。"""

import json
from pathlib import Path

import pricing_rules as rules

CATALOG = json.loads((Path(__file__).parent / "catalog.json").read_text(encoding="utf-8"))


def line_total(sku, qty):
    item = CATALOG[sku]
    return rules.apply_rules(item, qty)
