"""Provide shared pytest fixtures for the offline unit suite.

The module sets a safe default environment so ``get_settings()`` works without a
``.env`` file, and offers an ASGI client helper that runs the app lifespan plus a
fake Redis. It relates to ``libs/common/settings.py`` and ``pyproject.toml``.
Only task T-001 edits this file; other tasks add ``tests/unit/support_<area>.py``.

Pattern for in-memory fake repositories: write a small class in
``tests/unit/support_<area>.py`` that implements the same async methods as the
real repository with dicts, record calls in a list, and inject it by overriding
the FastAPI dependency (``app.dependency_overrides``) or by assigning it to the
service module attribute inside the test. Never connect to Postgres in unit tests.
"""

import os
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import pytest

DEFAULT_ENVIRONMENT = {
    "ENVIRONMENT": "test",
    "LOG_LEVEL": "WARNING",
    "INTERNAL_SERVICE_TOKEN": "test-internal-token",
    "REDIS_URL": "redis://localhost:6379/15",
}

for _name, _value in DEFAULT_ENVIRONMENT.items():
    os.environ.setdefault(_name, _value)


def _ignore_dotenv() -> None:
    """Stop ``Settings`` from reading a local ``.env``.

    ``make invent-tracker`` writes a ``.env`` with generated secrets and development logins. Without
    this, the next ``make test`` loads it and 108 tests fail on values that only exist on that
    machine. Tests that need a dotenv file pass ``_env_file`` explicitly.
    """
    try:
        from libs.common.settings import Settings
    except ModuleNotFoundError:  # runtime dependencies missing: pure-Python tests still run
        return
    Settings.model_config["env_file"] = None


_ignore_dotenv()


@pytest.fixture(autouse=True)
def _fresh_settings() -> Any:
    """Clear the cached settings around each test so environment changes apply."""
    try:
        from libs.common.settings import get_settings
    except ModuleNotFoundError:  # runtime dependencies missing: pure-Python tests still run
        yield
        return
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def asgi_client() -> Callable[[Any], AbstractAsyncContextManager[Any]]:
    """Return a factory: ``async with asgi_client(app) as client`` runs the lifespan."""

    @asynccontextmanager
    async def factory(app: Any, base_url: str = "http://test") -> AsyncIterator[Any]:
        import httpx

        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=transport, base_url=base_url) as client:
                yield client

    return factory


@pytest.fixture
async def fake_redis() -> AsyncIterator[Any]:
    """Provide an in-memory asyncio Redis client with decoded responses."""
    import fakeredis

    client = fakeredis.FakeAsyncRedis(decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()
