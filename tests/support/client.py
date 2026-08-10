"""Единая точка входа к мастеру: живой HTTP или приложение в процессе.

`TestClient` из starlette и `httpx.Client` имеют одинаковый интерфейс
(`get/post/patch/delete`, `.status_code`, `.json()`, `.text`), поэтому тестам
не нужно знать, в каком режиме они работают.
"""

from __future__ import annotations

import functools

from tests.support import env


@functools.lru_cache(maxsize=1)
def get_client():
    if env.LIVE:
        import httpx
        return httpx.Client(base_url=env.MASTER_URL, timeout=env.TIMEOUT)

    from tests.support import stubs

    stubs.install_dependency_stubs()

    import main  # импорт только после подмены тяжёлых зависимостей

    stubs.install_router_stub()
    stubs.install_agent_transport()

    from fastapi.testclient import TestClient
    return TestClient(main.app)


def reset() -> None:
    """Сбросить кэш клиента (нужно только при отладке тестов)."""
    get_client.cache_clear()
