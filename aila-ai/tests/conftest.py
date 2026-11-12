import pytest


@pytest.fixture
def anyio_backend() -> str:
    """异步测试只跑 asyncio"""
    return "asyncio"
