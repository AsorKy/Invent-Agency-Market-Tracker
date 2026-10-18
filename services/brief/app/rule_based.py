"""Build deterministic Brief content from company facts and tenant rules.

``RuleBasedSummarizer`` is the always-available summarizer (decision D-09) and
implements the replaceable ``Summarizer`` interface that a semantic model can
also implement. Alert codes and severities follow ARCH-0001 section E1.1;
thresholds, weights and disabled codes come from the tenant ``brief_rules``
(REQ-BRF-003). It relates to ``main.py`` and ``models.py``. Complexity is
O(alerts log alerts) per digest with no I/O.
"""

from dataclasses import dataclass
from typing import Protocol

from services.brief.app.models import (
    BriefAlert,
    BriefContent,
    BriefRules,
    CompanyFacts,
)

RULE_SUMMARIZER_VERSION = "rule-v3"
SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}
ENRICHMENT_FLAG_ALERTS = {
    "web_missing": ("WEB_MISSING", "Company has no web presence."),
    "stack_outdated": ("STACK_OUTDATED", "Company technology stack looks outdated."),
    "app_missing": ("APP_MISSING", "Company has no mobile app."),
}
NEXT_STEPS = {
    "RUES_QUARANTINE": "Resolve the NIT quarantine before qualification.",
    "RUES_NO_MATCH": "Verify the NIT manually; RUES returned no match.",
    "DOMAIN_NO_CERT": "Review the domain certificate and reputation before outreach.",
    "ENRICHMENT_FAILED": "Retry deep enrichment.",
    "NO_CONTACT": "Contact the lead.",
    "TASK_OVERDUE": "Complete the overdue commercial task.",
}


@dataclass(frozen=True)
class SummaryResult:
    """Carry generated content and the summarizer version that produced it."""

    content: BriefContent
    version: str


class Summarizer(Protocol):
    """Replaceable summarizer interface; implementations may be semantic models."""

    async def summarize(self, facts: CompanyFacts, rules: BriefRules) -> SummaryResult: ...


def build_alerts(facts: CompanyFacts, rules: BriefRules) -> list[BriefAlert]:
    """Evaluate every alert rule and drop the codes the tenant disabled."""
    thresholds = rules.alert_rules
    alerts: list[BriefAlert] = []
    if facts.rues_status == "quarantined":
        over = facts.quarantine_days > thresholds.max_quarantine_days
        suffix = f" for {facts.quarantine_days} days (max {thresholds.max_quarantine_days})"
        alerts.append(
            BriefAlert(
                code="RUES_QUARANTINE",
                severity="critical",
                message="NIT is in quarantine" + (suffix if over else "") + ".",
            )
        )
    elif (
        thresholds.require_rues_match
        and facts.rues_status == "unmatched"
        and facts.rues_attempts >= 2
    ):
        alerts.append(
            BriefAlert(
                code="RUES_NO_MATCH",
                severity="critical",
                message=f"No RUES match after {facts.rues_attempts} attempts.",
            )
        )
    if not facts.domain_certificate_ok:
        alerts.append(
            BriefAlert(
                code="DOMAIN_NO_CERT",
                severity="critical",
                message="Domain has no valid certificate; reputation may be compromised.",
            )
        )
    if facts.enrichment_failed:
        alerts.append(
            BriefAlert(
                code="ENRICHMENT_FAILED",
                severity="warning",
                message="Deep enrichment failed (timeout).",
            )
        )
    days = facts.days_since_contact
    if days is not None and days >= thresholds.no_contact_days_warning:
        critical = days >= thresholds.no_contact_days_critical
        alerts.append(
            BriefAlert(
                code="NO_CONTACT",
                severity="critical" if critical else "warning",
                message=f"No contact for {days} days.",
            )
        )
    for flag in sorted(set(facts.enrichment_flags)):
        if flag in ENRICHMENT_FLAG_ALERTS:
            code, message = ENRICHMENT_FLAG_ALERTS[flag]
            alerts.append(BriefAlert(code=code, severity="warning", message=message))
    if facts.overdue_tasks > 0:
        alerts.append(
            BriefAlert(
                code="TASK_OVERDUE",
                severity="info",
                message=f"{facts.overdue_tasks} commercial task(s) overdue.",
            )
        )
    disabled = set(rules.disabled_alert_codes)
    visible = [alert for alert in alerts if alert.code not in disabled]
    return sorted(visible, key=lambda a: (SEVERITY_ORDER[a.severity], a.code))


def compute_priority(facts: CompanyFacts, rules: BriefRules) -> float:
    """Sum the tenant weights of active signals plus the quarantine penalty."""
    total = sum(rules.weights.get(code, 0.0) for code in facts.signal_codes)
    if facts.rues_status == "quarantined":
        total += rules.weights.get("rues_quarantine_penalty", 0.0)
    return round(total, 4)


def build_headline(facts: CompanyFacts) -> str:
    """Compose a one-sentence headline from the known facts only."""
    parts = [facts.sector or "Unknown sector", facts.city or "Unknown city"]
    if facts.score is not None:
        parts.append(f"Score {facts.score}")
    parts.append(f"{len(facts.signal_codes)} active signals")
    return " - ".join(parts)


def build_rule_based_content(facts: CompanyFacts, rules: BriefRules | None = None) -> BriefContent:
    """Create digest content from facts and tenant rules."""
    rules = rules or BriefRules()
    alerts = build_alerts(facts, rules)
    steps = [NEXT_STEPS[a.code] for a in alerts if a.code in NEXT_STEPS]
    if facts.score is not None and facts.score >= rules.alert_rules.min_score_for_hot:
        steps.append("Hand the hot lead to the strategist for approval.")
    if not steps:
        steps.append("Collect verified company signals.")
    return BriefContent(
        headline=build_headline(facts),
        alerts=alerts,
        next_steps=steps,
        coverage={
            "company": facts.version > 0,
            "rues": facts.rues_status == "matched",
            "activity": bool(facts.activity),
        },
        activity=facts.activity,
        priority=compute_priority(facts, rules),
    )


class RuleBasedSummarizer:
    """Always-available summarizer used as the default and as the fallback."""

    async def summarize(self, facts: CompanyFacts, rules: BriefRules) -> SummaryResult:
        return SummaryResult(build_rule_based_content(facts, rules), RULE_SUMMARIZER_VERSION)
