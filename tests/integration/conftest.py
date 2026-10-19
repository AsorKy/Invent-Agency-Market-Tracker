"""Mark every test in this folder as an integration test.

Integration tests need real services (Postgres, Redis) and are excluded from
``make test``; the owner runs them with ``make test-integration``.
"""

import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Add the ``integration`` marker to tests collected under this directory."""
    here = Path(__file__).parent
    for item in items:
        if here in Path(str(item.fspath)).parents:
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    """A migrated scratch database, dropped after the test module."""
    from support_services import migrated_database

    with migrated_database() as url:
        yield url


@pytest.fixture
async def redis_stream() -> AsyncIterator[tuple[Any, str]]:
    """A real Redis client and a unique stream name that is deleted afterwards."""
    from uuid import uuid4

    from support_services import BASE_REDIS_URL

    from libs.common.redis_client import create_redis

    assert BASE_REDIS_URL
    redis = create_redis(BASE_REDIS_URL)
    stream = f"imt-test:{uuid4().hex}:events"
    try:
        yield redis, stream
    finally:
        await redis.delete(stream)
        await redis.aclose()
