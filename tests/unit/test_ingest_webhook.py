"""Unit tests for Bright Data webhook signature verification.

Covers REQ-QA-002 (offline unit coverage) and REQ-SEC-002 (timestamped HMAC webhook with a
replay window). ``valid_signed_webhook`` in ``libs/common/security.py`` is tested directly; when
``pydantic_settings`` is missing the module is loaded with a stub settings module so the
stdlib-only logic still runs. Endpoint tests drive ``services/ingest/app/main.py`` through the
ASGI client with a fake repository and skip when FastAPI dependencies are absent.
"""

import hashlib
import hmac
import importlib.util
import json
import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

SECRET = "unit-test-webhook-secret"
WINDOW = 300
NOW = 1_800_000_000.0
BODY = b'{"snapshot_id":"s-1","dataset_id":"d-1","payload":{"a":1}}'
ROOT = Path(__file__).resolve().parents[2]


def _load_security() -> Any:
    """Import ``libs.common.security``, stubbing settings when pydantic is unavailable."""
    if importlib.util.find_spec("pydantic_settings") is not None:
        from libs.common import security

        return security
    stub = types.ModuleType("libs.common.settings")
    stub.Settings = object  # type: ignore[attr-defined]
    stub.get_settings = lambda: None  # type: ignore[attr-defined]
    previous = sys.modules.get("libs.common.settings")
    sys.modules["libs.common.settings"] = stub
    try:
        spec = importlib.util.spec_from_file_location(
            "_security_under_test", ROOT / "libs" / "common" / "security.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            sys.modules.pop("libs.common.settings", None)
        else:
            sys.modules["libs.common.settings"] = previous
    return module


security = _load_security()


def sign(body: bytes, timestamp: str, secret: str = SECRET) -> str:
    """Compute the expected hex HMAC-SHA256 over ``<timestamp>.<body>``."""
    message = timestamp.encode("ascii") + b"." + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify(
    *,
    signature: str | None,
    timestamp: str | None,
    secret: str | None = SECRET,
    body: bytes = BODY,
    now: float = NOW,
) -> bool:
    return bool(
        security.valid_signed_webhook(
            body,
            signature=signature,
            timestamp=timestamp,
            secret=secret,
            replay_window_seconds=WINDOW,
            now=now,
        )
    )


def test_valid_signature_is_accepted() -> None:
    ts = str(int(NOW))
    assert verify(signature=sign(BODY, ts), timestamp=ts)


def test_valid_signature_with_sha256_prefix_is_accepted() -> None:
    ts = str(int(NOW))
    assert verify(signature="sha256=" + sign(BODY, ts), timestamp=ts)


def test_wrong_signature_is_rejected() -> None:
    ts = str(int(NOW))
    assert not verify(signature="0" * 64, timestamp=ts)
    assert not verify(signature=sign(BODY, ts, secret="other-secret"), timestamp=ts)


def test_tampered_body_is_rejected() -> None:
    ts = str(int(NOW))
    assert not verify(signature=sign(BODY, ts), timestamp=ts, body=BODY + b" ")


def test_signature_bound_to_timestamp() -> None:
    ts = str(int(NOW))
    other = str(int(NOW) - 1)
    assert not verify(signature=sign(BODY, other), timestamp=ts)


def test_stale_timestamp_is_rejected() -> None:
    ts = str(int(NOW - WINDOW - 1))
    assert not verify(signature=sign(BODY, ts), timestamp=ts)


def test_future_timestamp_outside_window_is_rejected() -> None:
    ts = str(int(NOW + WINDOW + 1))
    assert not verify(signature=sign(BODY, ts), timestamp=ts)


def test_timestamps_at_window_edge_are_accepted() -> None:
    for delta in (-WINDOW, WINDOW):
        ts = str(int(NOW + delta))
        assert verify(signature=sign(BODY, ts), timestamp=ts)


def test_missing_headers_are_rejected_when_secret_configured() -> None:
    ts = str(int(NOW))
    assert not verify(signature=None, timestamp=ts)
    assert not verify(signature=sign(BODY, ts), timestamp=None)
    assert not verify(signature="", timestamp="")


def test_non_numeric_timestamp_is_rejected() -> None:
    assert not verify(signature=sign(BODY, "abc"), timestamp="abc")


def test_verification_is_skipped_without_secret() -> None:
    assert verify(signature=None, timestamp=None, secret=None)
    assert verify(signature=None, timestamp=None, secret="")


def test_default_clock_is_used_when_now_is_omitted() -> None:
    import time

    ts = str(int(time.time()))
    assert security.valid_signed_webhook(
        BODY,
        signature=sign(BODY, ts),
        timestamp=ts,
        secret=SECRET,
        replay_window_seconds=WINDOW,
    )
    old = str(int(time.time()) - WINDOW - 60)
    assert not security.valid_signed_webhook(
        BODY,
        signature=sign(BODY, old),
        timestamp=old,
        secret=SECRET,
        replay_window_seconds=WINDOW,
    )


class FakeRepository:
    """Record persisted snapshots without touching Postgres or Redis."""

    calls: list[dict[str, Any]] = []

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    async def close(self) -> None:
        return None

    async def persist_snapshot(self, **kwargs: Any) -> tuple[str, bool, bool]:
        FakeRepository.calls.append(kwargs)
        return "00000000-0000-0000-0000-000000000001", True, True


@pytest.fixture
def ingest_app(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Return the ingest module with a configured secret and a fake repository."""
    pytest.importorskip("pydantic_settings")
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    pytest.importorskip("orjson")
    pytest.importorskip("structlog")
    pytest.importorskip("redis")
    pytest.importorskip("sqlalchemy")
    from services.ingest.app import main as ingest

    monkeypatch.setattr(ingest.settings, "bright_data_webhook_secret", SECRET)
    monkeypatch.setattr(ingest.settings, "bright_data_replay_window_seconds", WINDOW)
    monkeypatch.setattr(ingest, "IngestRepository", FakeRepository)
    FakeRepository.calls = []
    return ingest


def _headers(body: bytes, ts: str, signature: str | None = None) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-BrightData-Timestamp": ts,
        "X-BrightData-Signature": signature or sign(body, ts),
    }


async def _post(
    asgi_client: Callable[[Any], Any], ingest: Any, headers: dict[str, str], body: bytes = BODY
) -> Any:
    async with asgi_client(ingest.app) as client:
        return await client.post("/internal/ingest/webhook/bd", content=body, headers=headers)


async def test_endpoint_accepts_valid_signature(
    ingest_app: Any, asgi_client: Callable[[Any], Any]
) -> None:
    import time

    ts = str(int(time.time()))
    response = await _post(asgi_client, ingest_app, _headers(BODY, ts))
    assert response.status_code == 202
    assert response.json()["snapshot_id"] == "s-1"
    assert len(FakeRepository.calls) == 1


async def test_endpoint_rejects_wrong_signature(
    ingest_app: Any, asgi_client: Callable[[Any], Any]
) -> None:
    import time

    ts = str(int(time.time()))
    response = await _post(asgi_client, ingest_app, _headers(BODY, ts, signature="f" * 64))
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid_bright_data_signature"
    assert FakeRepository.calls == []


@pytest.mark.parametrize("offset", [-(WINDOW + 60), WINDOW + 60])
async def test_endpoint_rejects_stale_and_future_timestamps(
    ingest_app: Any, asgi_client: Callable[[Any], Any], offset: int
) -> None:
    import time

    ts = str(int(time.time()) + offset)
    response = await _post(asgi_client, ingest_app, _headers(BODY, ts))
    assert response.status_code == 401
    assert FakeRepository.calls == []


async def test_endpoint_rejects_missing_signature_headers(
    ingest_app: Any, asgi_client: Callable[[Any], Any]
) -> None:
    response = await _post(asgi_client, ingest_app, {"Content-Type": "application/json"})
    assert response.status_code == 401
    assert FakeRepository.calls == []


async def test_endpoint_signature_covers_raw_bytes(
    ingest_app: Any, asgi_client: Callable[[Any], Any]
) -> None:
    import time

    ts = str(int(time.time()))
    reformatted = json.dumps(json.loads(BODY), indent=2).encode()
    response = await _post(asgi_client, ingest_app, _headers(BODY, ts), body=reformatted)
    assert response.status_code == 401
