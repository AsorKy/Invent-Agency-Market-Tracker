"""The integration skip guard fails a run that skipped, only when asked to.

Runs a throwaway pytest session with ``tests/integration/forbid_skips.py`` loaded, so it needs no
services. It relates to ``tests/integration/conftest.py`` and the CI integration job.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "integration"

SAMPLE = """
import pytest

def test_runs():
    assert True

@pytest.mark.skip(reason="service missing")
def test_skipped():
    pass

@pytest.mark.xfail(reason="known gap")
def test_expected_failure():
    assert False
"""


def _run(tmp_path: Path, *, forbid: bool, tests: str = SAMPLE) -> subprocess.CompletedProcess[str]:
    (tmp_path / "test_sample.py").write_text(tests, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "INTEGRATION_FORBID_SKIPS"}
    env["PYTHONPATH"] = str(PLUGIN_DIR)
    if forbid:
        env["INTEGRATION_FORBID_SKIPS"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "forbid_skips", "-p", "no:cacheprovider"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_skip_fails_the_run_when_forbidden(tmp_path: Path) -> None:
    result = _run(tmp_path, forbid=True)
    assert result.returncode == pytest.ExitCode.TESTS_FAILED
    assert "skipped tests are not allowed" in result.stdout
    assert "test_sample.py::test_skipped" in result.stdout
    assert "test_expected_failure" not in result.stdout.split("skipped tests are not allowed")[1]


def test_a_skip_is_fine_when_not_forbidden(tmp_path: Path) -> None:
    assert _run(tmp_path, forbid=False).returncode == pytest.ExitCode.OK


def test_a_run_without_skips_passes_when_forbidden(tmp_path: Path) -> None:
    result = _run(tmp_path, forbid=True, tests="def test_runs():\n    assert True\n")
    assert result.returncode == pytest.ExitCode.OK
