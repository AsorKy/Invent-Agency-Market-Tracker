"""Unit tests for RUES entity resolution (REQ-ING-003) using mocks only."""

from datetime import UTC, datetime

from libs.domain.entity_resolution import (
    Candidate,
    EntityResolver,
    InMemoryResolutionStore,
    MockRuesLookup,
    RuesRecord,
    classify_match,
    normalize_nit,
    score_names,
)

NOW = datetime(2026, 10, 18, tzinfo=UTC)
RUES = [RuesRecord("900123456", "Panaderia El Trigo S.A.S.", "Medellin")]


def _resolver(lookup: MockRuesLookup) -> tuple[EntityResolver, InMemoryResolutionStore]:
    store = InMemoryResolutionStore()
    return EntityResolver(lookup, store, now=NOW), store


def test_score_names_and_nit_normalization() -> None:
    assert score_names("Panaderia El Trigo SAS", "PANADERIA EL TRIGO") == 1.0
    assert score_names("Trigo El Panaderia", "Panaderia El Trigo") == 1.0
    assert score_names("", "x") == 0.0
    assert normalize_nit("900.123.456-7") == "900123456"
    assert normalize_nit(None) is None


def test_thresholds_in_classify_match() -> None:
    assert classify_match(0.95, rues_nit_match=True) == "matched"
    assert classify_match(0.9, rues_nit_match=True) == "manual_review"
    assert classify_match(0.95) == "manual_review"
    assert classify_match(0.6) == "manual_review"
    assert classify_match(0.59) == "quarantine"


def test_nit_match_above_threshold_is_automatic() -> None:
    resolver, store = _resolver(MockRuesLookup(RUES))
    out = resolver.resolve(Candidate("c1", "Panaderia El Trigo", "Medellin", "900.123.456-7"))
    assert out.decision == "matched" and out.rues_nit == "900123456"
    assert store.match_log == [] and store.queue == {}
    assert store.states["c1"] == ("matched", 1)


def test_borderline_goes_to_match_log() -> None:
    resolver, store = _resolver(MockRuesLookup(RUES))
    out = resolver.resolve(Candidate("c2", "Panaderia Trigo Dorado", None, "900123456"))
    assert out.decision == "manual_review" and 0.6 <= out.score <= 0.9
    assert store.match_log == [("c2", "900123456", out.score, "manual_review")]
    assert store.queue == {}


def test_name_only_match_never_auto_matches() -> None:
    resolver, store = _resolver(MockRuesLookup(RUES))
    out = resolver.resolve(Candidate("c3", "Panaderia El Trigo", "Medellin", None))
    assert out.decision == "manual_review"
    assert store.match_log[0][3] == "manual_review"


def test_no_match_queues_lookup_then_quarantines_after_two_attempts() -> None:
    lookup = MockRuesLookup(RUES)
    resolver, store = _resolver(lookup)
    first = resolver.resolve(Candidate("c4", "Zapateria Nueva", "Cali", "800000001"))
    assert first.decision == "new_company"
    assert store.queue["c4"]["attempts"] == 1 and store.queue["c4"]["status"] == "queued"
    assert store.quarantined == []

    second = resolver.resolve(Candidate("c4", "Zapateria Nueva", "Cali", "800000001", attempts=1))
    assert second.decision == "quarantine"
    assert store.quarantined and store.quarantined[0][0] == "c4"
    assert store.queue["c4"]["status"] == "quarantined"
    assert store.states["c4"] == ("quarantined", 2)


def test_retry_resolves_when_rues_later_matches() -> None:
    lookup = MockRuesLookup()
    resolver, store = _resolver(lookup)
    resolver.resolve(Candidate("c5", "Panaderia El Trigo", None, "900123456"))
    lookup.records.extend(RUES)
    out = resolver.resolve(Candidate("c5", "Panaderia El Trigo", None, "900123456", attempts=1))
    assert out.decision == "matched"
    assert store.queue["c5"]["status"] == "resolved"
    assert store.quarantined == []


def test_rues_failure_counts_as_attempt_and_records_error() -> None:
    resolver, store = _resolver(MockRuesLookup(RUES, fail=True))
    first = resolver.resolve(Candidate("c6", "Panaderia El Trigo", None, "900123456"))
    assert first.decision == "retry_later"
    assert store.queue["c6"]["last_error"] == "mock RUES unavailable"
    assert store.queue["c6"]["next_attempt_at"] > NOW
    second = resolver.resolve(Candidate("c6", "Panaderia El Trigo", None, "900123456", attempts=1))
    assert second.decision == "quarantine"
    assert store.quarantined[0][1] == "mock RUES unavailable"
