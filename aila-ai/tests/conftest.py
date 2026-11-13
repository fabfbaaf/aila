from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from app import deps
from app.main import app, get_embedder


@pytest.fixture
def anyio_backend() -> str:
    """异步测试只跑 asyncio"""
    return "asyncio"


@pytest.fixture(autouse=True)
def no_real_embedder() -> Iterator[None]:
    """接口测试默认不加载真实的向量模型（慢，还要下载）；需要的测试自己换成假的"""
    app.dependency_overrides[get_embedder] = lambda: None
    yield
    app.dependency_overrides.pop(get_embedder, None)


@pytest.fixture(autouse=True)
def isolated_data(tmp_path: Path) -> Iterator[None]:
    """所有测试的本机数据（资料库）都放临时目录，不碰 aila-ai/data"""
    saved = deps.settings
    deps.settings = replace(saved, data_dir=tmp_path / "data")
    deps.get_knowledge.cache_clear()
    deps.get_registry.cache_clear()
    yield
    deps.settings = saved
    deps.get_knowledge.cache_clear()
    deps.get_registry.cache_clear()
