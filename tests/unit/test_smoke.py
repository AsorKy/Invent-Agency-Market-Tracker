"""Placeholder suite proving the test tooling and default environment work."""

import os

import pytest


def test_default_environment_loads_settings() -> None:
    pytest.importorskip("pydantic_settings")
    from libs.common.settings import get_settings

    assert get_settings().environment == os.environ["ENVIRONMENT"]
