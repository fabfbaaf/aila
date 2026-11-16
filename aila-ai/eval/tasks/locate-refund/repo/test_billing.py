"""只读项目自带测试，保证查到的退款接口行为明确。"""

from billing import process_refund
from orders import create_order


def test_refund() -> None:
    assert process_refund(create_order(20), 5)["refunded"] == 5
