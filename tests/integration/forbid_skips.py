"""Pytest plugin: fail an integration run that skipped tests (``INTEGRATION_FORBID_SKIPS=1``).

Integration tests skip themselves when ``TEST_DATABASE_URL``, ``TEST_REDIS_URL`` or an extra
such as ``psycopg`` is missing. That is right on a laptop and wrong in CI, where a skip means the
stage went green without running the test. The CI integration job sets the variable; local runs
do not. It relates to ``conftest.py`` (which loads it) and ``tests/unit/test_forbid_skips.py``.
"""

import os

import pytest

_skipped: dict[str, None] = {}


def _enabled() -> bool:
    return os.environ.get("INTEGRATION_FORBID_SKIPS") == "1"


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if _enabled() and report.skipped and not hasattr(report, "wasxfail"):
        _skipped[report.nodeid] = None


def pytest_sessionfinish(session: pytest.Session) -> None:
    if _skipped and session.exitstatus == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    if _skipped:
        terminalreporter.write_sep(
            "=", "skipped tests are not allowed (INTEGRATION_FORBID_SKIPS=1)", red=True
        )
        for nodeid in _skipped:
            terminalreporter.write_line(nodeid)
