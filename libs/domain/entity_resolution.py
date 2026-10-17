"""Normalize company names and classify entity matches.

Normalization removes accents, legal suffixes, and punctuation; classification
turns a score and NIT/RUES match into an operational decision. It relates to
``services/ingest/app/repository.py`` and future F1 RUES lookups. It is used to
produce reproducible candidates and separate automatic matches, manual review,
and quarantine.
"""

import re
import unicodedata


def normalize_company_name(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    normalized = re.sub(r"\bS\s*\.?\s*A\s*\.?\s*S?\s*\.?\b", " ", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\b(LTDA|LIMITADA)\.?\b", " ", normalized, flags=re.IGNORECASE)
    return re.sub(r"[^A-Z0-9]+", " ", normalized.upper()).strip()


def classify_match(score: float, *, rues_nit_match: bool = False) -> str:
    if rues_nit_match and score >= 0.9:
        return "matched"
    if score >= 0.6:
        return "manual_review"
    return "quarantine"
