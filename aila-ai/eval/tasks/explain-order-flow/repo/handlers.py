"""HTTP 入口。"""

from orders import place_order


def handle_order(request):
    return {"status": 201, "order": place_order(request["user"], request["items"])}
