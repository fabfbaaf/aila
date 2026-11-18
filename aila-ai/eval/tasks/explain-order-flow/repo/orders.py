"""下单。"""

from inventory import reserve_stock
from repository import save_order
from validation import validate_order


def place_order(user, items):
    validate_order(items)
    reserve_stock(items)
    return save_order(user, items)


def cancel_order(order_id):
    return {"cancelled": order_id}
