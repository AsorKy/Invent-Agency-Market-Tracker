"""Redact sensitive headers, PII-like text, and event payloads from logs.

The helpers are intended for gateway request diagnostics and structlog
processors. They relate to ``services/gateway/app/main.py`` and can later be
registered in ``libs.common.logging`` once all processes use the same
redaction policy. Their purpose is to keep cookies, authorization material,
CSRF tokens, edge secrets, document numbers, and non-agency email addresses
out of application logs.
"""

import os
import re
from collections.abc import Mapping, MutableMapping
from typing import Any

REDACTED = "[REDACTED]"

SENSITIVE_HEADER_NAMES = frozenset(
    {
        "authorization",
        "cookie",
        "proxy-authorization",
        "set-cookie",
        "x-csrf-token",
        "x-edge-secret",
    }
)
SENSITIVE_LOG_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "headers",
        "payload",
        "request_body",
        "response_body",
        "secret",
        "token",
        "x-csrf-token",
        "x-edge-secret",
    }
)
_DOCUMENT_NUMBER_PATTERN = re.compile(r"\b\d{6,12}-(?P<check_digit>\d)\b")
_EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@(?P<domain>[A-Z0-9.-]+\.[A-Z]{2,})\b", re.I)


def _allowed_email_domains() -> frozenset[str]:
    """Read the optional comma-separated agency email domain allowlist."""

    raw_domains = os.getenv("LOG_ALLOWED_EMAIL_DOMAINS", "")
    return frozenset(
        domain.strip().lower().lstrip("@") for domain in raw_domains.split(",") if domain.strip()
    )


def redact_text(value: str, *, allowed_email_domains: frozenset[str] | None = None) -> str:
    """Mask document numbers and email addresses outside the agency allowlist."""

    domains = _allowed_email_domains() if allowed_email_domains is None else allowed_email_domains

    def redact_document(match: re.Match[str]) -> str:
        return f"***-***-***-{match.group('check_digit')}"

    value = _DOCUMENT_NUMBER_PATTERN.sub(redact_document, value)

    def redact_email(match: re.Match[str]) -> str:
        domain = match.group("domain").lower()
        if domain in domains:
            return match.group(0)
        return f"c***@{domain}"

    return _EMAIL_PATTERN.sub(redact_email, value)


def redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Return headers safe for diagnostics without exposing security tokens."""

    redacted: dict[str, str] = {}
    for name, value in headers.items():
        normalized_name = name.lower()
        redacted[name] = (
            REDACTED if normalized_name in SENSITIVE_HEADER_NAMES else redact_text(str(value))
        )
    return redacted


def redact_log_record(
    _logger: Any,
    _method_name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """Provide a structlog processor that removes sensitive log fields."""

    for key, value in list(event_dict.items()):
        normalized_key = key.lower()
        if normalized_key in SENSITIVE_LOG_KEYS:
            if normalized_key == "headers" and isinstance(value, Mapping):
                event_dict[key] = redact_headers(value)
            else:
                event_dict[key] = REDACTED
        elif isinstance(value, str):
            event_dict[key] = redact_text(value)
    return event_dict
