"""Check the go-live runbook against the environment template (REQ-DOC-001).

The runbook must exist, cover each required topic, and cite only environment
variable names that appear in ``.env.example`` (plus the documented
``VERIFY_LIVE_*`` overrides read by ``ops/verify_live.py``). It must contain no
non-ASCII text. It relates to ``docs/runbooks/go-live.md``.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = ROOT / "docs" / "runbooks" / "go-live.md"
ENV_EXAMPLE = ROOT / ".env.example"
OVERRIDES = {"VERIFY_LIVE_INGEST_URL", "VERIFY_LIVE_CORE_URL", "VERIFY_LIVE_DATABASE_URL"}
# Repository secrets of the backups workflow: GitHub secrets, not entries of .env.
BACKUP_SECRETS = {
    "BACKUP_SOURCE_URL",
    "BACKUP_EPHEMERAL_ADMIN_URL",
    "BACKUP_EPHEMERAL_PASSWORD",
    "BACKUP_AGE_RECIPIENT",
}
# Read by libs/domain/funnel.py only; deliberately not listed in .env.example.
RUNTIME_ONLY = {"FUNNEL_ALLOW_EXAMPLE_CHECKLISTS"}
PLACEHOLDER = re.compile(r"SOURCE_<NAME>_(ENABLED|DATASET_ID)")


def _env_names() -> set[str]:
    names = set()
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        match = re.match(r"([A-Z][A-Z0-9_]*)=", line)
        if match:
            names.add(match.group(1))
    return names


def test_runbook_is_ascii() -> None:
    RUNBOOK.read_text(encoding="ascii")


def test_runbook_cites_only_known_variables() -> None:
    text = PLACEHOLDER.sub("", RUNBOOK.read_text(encoding="ascii"))
    cited = set(re.findall(r"`([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)(?:=[^`]*)?`", text))
    unknown = cited - _env_names() - OVERRIDES - BACKUP_SECRETS - RUNTIME_ONLY
    assert not unknown, f"variables not in .env.example: {sorted(unknown)}"
    assert cited & _env_names()


def test_runbook_covers_required_topics() -> None:
    text = RUNBOOK.read_text(encoding="ascii")
    for needle in (
        "BRIGHT_DATA_API_TOKEN",
        "BRIGHT_DATA_WEBHOOK_SECRET",
        "PUBLIC_WEBHOOK_BASE_URL",
        "INGEST_POLL_ENABLED",
        "unverified",
        "make doctor",
        "make verify-live",
        "make invent-tracker",
        "VERIFY_LIVE_INGEST_URL",
        "VERIFY_LIVE_CORE_URL",
        "/api/v1",
        "FRONTEND_ORIGIN",
        "APP_PROFILE=local",
        "Troubleshooting",
    ):
        assert needle in text, needle


def test_every_source_is_documented() -> None:
    text = RUNBOOK.read_text(encoding="ascii")
    for name in (
        "MAPS",
        "COMPUTRABAJO",
        "LINKEDIN_COMPANIES",
        "LINKEDIN_JOBS",
        "INSTAGRAM",
        "RUES",
    ):
        assert f"SOURCE_{name}_ENABLED" in text
        assert f"SOURCE_{name}_DATASET_ID" in text


def test_troubleshooting_table_has_rows() -> None:
    text = RUNBOOK.read_text(encoding="ascii")
    section = text.split("## 10. Troubleshooting", 1)[1]
    rows = [line for line in section.splitlines() if line.startswith("| ")]
    assert len(rows) >= 8


def test_lens_wave_sections_are_present() -> None:
    text = RUNBOOK.read_text(encoding="ascii")
    assert "## 10. Troubleshooting" in text
    for number in range(11, 16):
        assert f"\n## {number}. " in text, number
    for needle in (
        "pulse-internal-header",
        "drift-alert-header",
        "60 seconds",
        "--profile lens",
        "DRIFT_TENANT_ID",
        "FUNNEL_ALLOW_EXAMPLE_CHECKLISTS",
        "horizon_mix_deviation",
        "LTV:CAC",
    ):
        assert needle in text, needle
