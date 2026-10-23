"""Test the production caller check of ops/check_configuration.py (D-28, T-184)."""

from ops.check_configuration import production_caller_problems


def test_local_profiles_may_carry_seed_credentials() -> None:
    env = {"APP_PROFILE": "local", "ENVIRONMENT": "ci", "SEED_SERVICE_TOKEN": "x"}
    assert production_caller_problems(env) == []


def test_production_fails_with_seed_or_verify_credentials() -> None:
    env = {
        "APP_PROFILE": "production",
        "ENVIRONMENT": "production",
        "SEED_SERVICE_TOKEN": "x",
        "VERIFY_LIVE_SECRET": "y",
        "SEED_TENANT_ID": "z",
        "INTERNAL_SERVICE_TOKEN": "k",
    }
    assert production_caller_problems(env) == ["SEED_SERVICE_TOKEN", "VERIFY_LIVE_SECRET"]


def test_production_environment_alone_is_enough() -> None:
    env = {"ENVIRONMENT": "production", "VERIFY_LIVE_TOKEN": "y"}
    assert production_caller_problems(env) == ["VERIFY_LIVE_TOKEN"]


def test_production_without_those_credentials_passes() -> None:
    env = {"APP_PROFILE": "production", "ENVIRONMENT": "production", "SEED_TOKEN": " "}
    assert production_caller_problems(env) == []
