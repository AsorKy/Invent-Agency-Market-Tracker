"""Mark every test in this folder as an integration test.

Integration tests need real services (Postgres, Redis) and are excluded from
``make test``; the owner runs them with ``make test-integration``.
"""

from pathlib import Path

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Add the ``integration`` marker to tests collected under this directory."""
    here = Path(__file__).parent
    for item in items:
        if here in Path(str(item.fspath)).parents:
            item.add_marker(pytest.mark.integration)
