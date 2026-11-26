from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from app import deps, security, winsock
from app.main import app, get_embedder

# Windows 上建事件循环偶尔卡死在 socketpair 的 accept 上（见 app/winsock.py），测试一卡就是整个卡住
winsock.install()


@pytest.fixture
def anyio_backend() -> str:
    """异步测试只跑 asyncio"""
    return "asyncio"


@pytest.fixture(autouse=True)
def no_real_embedder(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """测试默认不加载真实的向量模型（慢，还要下载）；需要的测试自己换成假的。
    接口经依赖注入拿，代码库在用到时直接调 deps.get_embedder，两处都换掉"""
    app.dependency_overrides[get_embedder] = lambda: None
    monkeypatch.setattr(deps, "get_embedder", lambda: None)
    yield
    app.dependency_overrides.pop(get_embedder, None)


@pytest.fixture(autouse=True)
def isolated_data(tmp_path: Path) -> Iterator[None]:
    """所有测试的本机数据（资料库）都放临时目录，不碰 aila-ai/data"""
    saved = deps.settings
    deps.settings = replace(saved, data_dir=tmp_path / "data")
    caches = (deps.get_knowledge, deps.get_code_projects, deps.get_registry, deps.get_runtime, deps.get_memory_store,
              deps.get_phone, deps.get_phone_server, deps.get_android_settings, deps.get_notifier,
              deps.get_schedules, deps.get_scheduler)
    for cache in caches:
        cache.cache_clear()
    yield
    deps.settings = saved
    for cache in caches:
        cache.cache_clear()


# TestClient 用 testserver、有的测试用 test 当 Host：只在测试进程里认它们（正式运行只认 127.0.0.1、localhost、[::1]）
security.EXTRA_HOSTS.update({"testserver", "test"})
