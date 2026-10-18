"""Normalize company names and resolve entity candidates against RUES.

Normalization removes accents, legal suffixes, and punctuation; classification
turns a score and NIT/RUES match into an operational decision. On top of that,
``EntityResolver`` implements REQ-ING-003 (ADR-IMT-0001 section 5.3): a score
above 0.9 by NIT is an automatic match, 0.6 to 0.9 goes to the manual review log,
and anything else becomes a new company queued for a RUES lookup; a candidate
without a RUES match after two attempts is quarantined.

The scorer is pure Python (``difflib`` token-sort ratio) so no extra dependency
is needed. RUES access and persistence sit behind the ``RuesLookup`` and
``ResolutionStore`` protocols, so everything runs without Postgres or network.
It relates to ``services/ingest/app/repository.py`` and
``services/ingest/app/resolution.py``.
"""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Protocol

AUTO_MATCH_THRESHOLD = 0.9
REVIEW_THRESHOLD = 0.6
MAX_RESOLUTION_ATTEMPTS = 2
RETRY_BACKOFF = timedelta(hours=1)


def normalize_company_name(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    normalized = re.sub(r"\bS\s*\.?\s*A\s*\.?\s*S?\s*\.?\b", " ", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\b(LTDA|LIMITADA)\.?\b", " ", normalized, flags=re.IGNORECASE)
    return re.sub(r"[^A-Z0-9]+", " ", normalized.upper()).strip()


def classify_match(score: float, *, rues_nit_match: bool = False) -> str:
    if rues_nit_match and score > AUTO_MATCH_THRESHOLD:
        return "matched"
    if score >= REVIEW_THRESHOLD:
        return "manual_review"
    return "quarantine"


def score_names(left: str, right: str) -> float:
    """Return a 0..1 similarity of two company names (token-sort ratio)."""
    a = " ".join(sorted(normalize_company_name(left).split()))
    b = " ".join(sorted(normalize_company_name(right).split()))
    if not a or not b:
        return 0.0
    return round(SequenceMatcher(None, a, b).ratio(), 4)


def normalize_nit(nit: str | None) -> str | None:
    """Keep digits of a NIT and drop the check digit separated by a hyphen."""
    if not nit:
        return None
    digits = re.sub(r"\D", "", nit.split("-")[0])
    return digits or None


@dataclass(frozen=True)
class RuesRecord:
    nit: str
    legal_name: str
    city: str | None = None


class RuesUnavailableError(Exception):
    """Raised by a ``RuesLookup`` when RUES cannot answer."""


class RuesLookup(Protocol):
    def by_nit(self, nit: str) -> RuesRecord | None: ...

    def search_by_name(self, name: str, city: str | None) -> Sequence[RuesRecord]: ...


class MockRuesLookup:
    """In-memory RUES used in tests and in the sandbox (no live service)."""

    def __init__(self, records: Sequence[RuesRecord] = (), *, fail: bool = False) -> None:
        self.records = list(records)
        self.fail = fail
        self.calls = 0

    def by_nit(self, nit: str) -> RuesRecord | None:
        self.calls += 1
        if self.fail:
            raise RuesUnavailableError("mock RUES unavailable")
        wanted = normalize_nit(nit)
        return next((r for r in self.records if normalize_nit(r.nit) == wanted), None)

    def search_by_name(self, name: str, city: str | None) -> Sequence[RuesRecord]:
        self.calls += 1
        if self.fail:
            raise RuesUnavailableError("mock RUES unavailable")
        return [r for r in self.records if city is None or r.city is None or r.city == city]


@dataclass(frozen=True)
class Candidate:
    id: str
    name: str
    city: str | None = None
    nit: str | None = None
    attempts: int = 0


@dataclass(frozen=True)
class Resolution:
    """Outcome: matched, manual_review, new_company, retry_later or quarantine."""

    decision: str
    score: float = 0.0
    rues_nit: str | None = None


class ResolutionStore(Protocol):
    """Persistence port over ingest.entity_candidate and related tables."""

    def log_match(
        self, candidate_id: str, rues_nit: str | None, score: float, decision: str
    ) -> None: ...

    def set_candidate_state(self, candidate_id: str, status: str, attempts: int) -> None: ...

    def enqueue_rues_lookup(
        self,
        candidate_id: str,
        attempts: int,
        next_attempt_at: datetime | None,
        last_error: str | None,
    ) -> None: ...

    def close_rues_lookup(self, candidate_id: str, status: str) -> None: ...

    def quarantine(self, candidate_id: str, reason: str) -> None: ...


@dataclass
class InMemoryResolutionStore:
    """Store double that records every write; also usable as a dry-run store."""

    match_log: list[tuple[str, str | None, float, str]] = field(default_factory=list)
    states: dict[str, tuple[str, int]] = field(default_factory=dict)
    queue: dict[str, dict[str, object]] = field(default_factory=dict)
    quarantined: list[tuple[str, str]] = field(default_factory=list)

    def log_match(
        self, candidate_id: str, rues_nit: str | None, score: float, decision: str
    ) -> None:
        self.match_log.append((candidate_id, rues_nit, score, decision))

    def set_candidate_state(self, candidate_id: str, status: str, attempts: int) -> None:
        self.states[candidate_id] = (status, attempts)

    def enqueue_rues_lookup(
        self,
        candidate_id: str,
        attempts: int,
        next_attempt_at: datetime | None,
        last_error: str | None,
    ) -> None:
        self.queue[candidate_id] = {
            "status": "queued",
            "attempts": attempts,
            "next_attempt_at": next_attempt_at,
            "last_error": last_error,
        }

    def close_rues_lookup(self, candidate_id: str, status: str) -> None:
        if candidate_id in self.queue:
            self.queue[candidate_id]["status"] = status

    def quarantine(self, candidate_id: str, reason: str) -> None:
        self.quarantined.append((candidate_id, reason))


class EntityResolver:
    """Resolve candidates against RUES; every call is one resolution attempt."""

    def __init__(self, rues: RuesLookup, store: ResolutionStore, *, now: datetime) -> None:
        self.rues = rues
        self.store = store
        self.now = now

    def resolve(self, candidate: Candidate) -> Resolution:
        attempts = candidate.attempts + 1
        error: str | None = None
        best: tuple[float, RuesRecord, bool] | None = None
        try:
            best = self._best_match(candidate)
        except RuesUnavailableError as exc:
            error = str(exc)

        if best is not None:
            score, record, by_nit = best
            decision = classify_match(score, rues_nit_match=by_nit)
            if decision == "matched":
                self.store.set_candidate_state(candidate.id, "matched", attempts)
                self.store.close_rues_lookup(candidate.id, "resolved")
                return Resolution("matched", score, record.nit)
            if decision == "manual_review":
                self.store.log_match(candidate.id, record.nit, score, "manual_review")
                self.store.set_candidate_state(candidate.id, "manual_review", attempts)
                self.store.close_rues_lookup(candidate.id, "resolved")
                return Resolution("manual_review", score, record.nit)

        # No usable RUES match: this attempt failed.
        if attempts >= MAX_RESOLUTION_ATTEMPTS:
            reason = error or f"no RUES match after {attempts} attempts"
            self.store.quarantine(candidate.id, reason)
            self.store.set_candidate_state(candidate.id, "quarantined", attempts)
            self.store.close_rues_lookup(candidate.id, "quarantined")
            return Resolution("quarantine")
        self.store.set_candidate_state(candidate.id, "pending_rues", attempts)
        self.store.enqueue_rues_lookup(candidate.id, attempts, self.now + RETRY_BACKOFF, error)
        return Resolution("retry_later" if error else "new_company")

    def _best_match(self, candidate: Candidate) -> tuple[float, RuesRecord, bool] | None:
        nit = normalize_nit(candidate.nit)
        if nit:
            record = self.rues.by_nit(nit)
            if record is not None:
                return score_names(candidate.name, record.legal_name), record, True
        scored = [
            (score_names(candidate.name, r.legal_name), r)
            for r in self.rues.search_by_name(candidate.name, candidate.city)
        ]
        if not scored:
            return None
        score, record = max(scored, key=lambda item: item[0])
        return score, record, False
