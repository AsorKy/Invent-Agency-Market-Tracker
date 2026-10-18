"""Unit tests for per-tenant Brief alert rules (REQ-BRF-003)."""

import pytest

pytest.importorskip("pydantic")
from services.brief.app.models import AlertRules, BriefRules, CompanyFacts  # noqa: E402
from services.brief.app.rule_based import build_alerts, build_rule_based_content  # noqa: E402
from services.brief.app.service import BriefService  # noqa: E402
from tests.unit.support_brief import COMPANY  # noqa: E402


def _facts(**kw: object) -> CompanyFacts:
    return CompanyFacts(company_id=COMPANY, **kw)  # type: ignore[arg-type]


def _codes(facts: CompanyFacts, rules: BriefRules | None = None) -> dict[str, str]:
    return {a.code: a.severity for a in build_alerts(facts, rules or BriefRules())}


def test_no_alerts_for_a_healthy_company() -> None:
    assert _codes(_facts(rues_status="matched", days_since_contact=1)) == {}


def test_catalog_codes_and_severities() -> None:
    facts = _facts(
        rues_status="quarantined",
        quarantine_days=9,
        domain_certificate_ok=False,
        enrichment_failed=True,
        days_since_contact=6,
        enrichment_flags=["web_missing", "stack_outdated", "app_missing"],
        overdue_tasks=2,
    )
    assert _codes(facts) == {
        "RUES_QUARANTINE": "critical",
        "DOMAIN_NO_CERT": "critical",
        "ENRICHMENT_FAILED": "warning",
        "NO_CONTACT": "warning",
        "WEB_MISSING": "warning",
        "STACK_OUTDATED": "warning",
        "APP_MISSING": "warning",
        "TASK_OVERDUE": "info",
    }


def test_alerts_are_sorted_by_severity() -> None:
    facts = _facts(domain_certificate_ok=False, overdue_tasks=1, enrichment_failed=True)
    severities = [a.severity for a in build_alerts(facts, BriefRules())]
    assert severities == ["critical", "warning", "info"]


def test_no_contact_thresholds_come_from_tenant_rules() -> None:
    rules = BriefRules(
        alert_rules=AlertRules(no_contact_days_warning=2, no_contact_days_critical=4)
    )
    assert _codes(_facts(days_since_contact=1), rules) == {}
    assert _codes(_facts(days_since_contact=2), rules) == {"NO_CONTACT": "warning"}
    assert _codes(_facts(days_since_contact=4), rules) == {"NO_CONTACT": "critical"}
    assert _codes(_facts(days_since_contact=4)) == {}


def test_rues_no_match_needs_two_attempts_and_the_tenant_flag() -> None:
    one = _facts(rues_status="unmatched", rues_attempts=1)
    two = _facts(rues_status="unmatched", rues_attempts=2)
    assert _codes(one) == {}
    assert _codes(two) == {"RUES_NO_MATCH": "critical"}
    relaxed = BriefRules(alert_rules=AlertRules(require_rues_match=False))
    assert _codes(two, relaxed) == {}


def test_disabled_alert_codes_are_dropped() -> None:
    rules = BriefRules(disabled_alert_codes=["STACK_OUTDATED"])
    facts = _facts(enrichment_flags=["stack_outdated", "web_missing"])
    assert _codes(facts, rules) == {"WEB_MISSING": "warning"}


def test_weights_drive_priority_including_quarantine_penalty() -> None:
    rules = BriefRules(
        weights={
            "signal_recent_hiring": 2.5,
            "signal_new_cto": 3.0,
            "rues_quarantine_penalty": -2.0,
        }
    )
    facts = _facts(
        signal_codes=["signal_recent_hiring", "signal_new_cto", "unknown"],
        rues_status="quarantined",
    )
    assert build_rule_based_content(facts, rules).priority == 3.5
    assert build_rule_based_content(facts).priority == 0.0


def test_rules_parse_the_documented_json_and_ignore_extras() -> None:
    rules = BriefRules(
        **{
            "alert_rules": {
                "no_contact_days_warning": 5,
                "no_contact_days_critical": 15,
                "require_rues_match": True,
                "min_score_for_hot": 70,
                "min_score_for_qualified": 50,
                "max_quarantine_days": 7,
            },
            "weights": {"signal_new_cto": 3.0},
            "disabled_alert_codes": ["STACK_OUTDATED"],
        }
    )
    assert rules.alert_rules.max_quarantine_days == 7
    assert rules.disabled_alert_codes == ["STACK_OUTDATED"]


def test_hot_score_adds_a_next_step() -> None:
    content = build_rule_based_content(_facts(score=80), BriefRules())
    assert any("hot lead" in step for step in content.next_steps)
    assert "Score 80" in content.headline


def test_config_update_makes_older_digest_stale() -> None:
    content = build_rule_based_content(_facts(), BriefRules())
    from services.brief.app.models import StoredDigest

    digest = StoredDigest(
        company_id=COMPANY,
        source_version=1,
        content=content,
        generated_at=1,
        summarizer_version="rule-v3",
        degraded=False,
        config_version=1,
    )
    assert BriefService.is_fresh(digest, 1, 1)
    assert not BriefService.is_fresh(digest, 1, 2)
