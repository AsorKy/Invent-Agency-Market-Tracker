"""Define the L2 event chain and trace one decision end to end (REQ-LNS-012).

The chain is ``signal.created`` -> ``signal.tagged`` -> ``signal.scored`` ->
``company.matched`` -> ``signal.explained`` -> ``funnel.gate_decision`` ->
``project.pipeline_updated`` -> ``project.forecast_updated`` (ADR-IMT-0002
section 2.6). The envelope in ``libs/contracts/events.py`` is not changed: every
chain event carries ``source_version`` and a ``parent`` block inside its payload,
so the rule is additive and needs no migration. Producers call ``stamp_payload``;
audits call ``trace_chain``. Pure functions only, no I/O.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

CHAIN: tuple[str, ...] = (
    "signal.created",
    "signal.tagged",
    "signal.scored",
    "company.matched",
    "signal.explained",
    "funnel.gate_decision",
    "project.pipeline_updated",
    "project.forecast_updated",
)
_POSITION = {name: index for index, name in enumerate(CHAIN)}
SEPARATOR = "<"


def _source_version_of(event: Mapping[str, Any]) -> str:
    payload = event.get("payload") or {}
    value = event.get("source_version") or payload.get("source_version")
    return str(value) if value else ""


def build_source_version(producer_version: str, parent_source_version: str | None = None) -> str:
    """Return the child version: own producer version, then the parent's, joined by ``<``."""
    if not producer_version:
        raise ValueError("producer_version must not be empty")
    if not parent_source_version:
        return producer_version
    return f"{producer_version}{SEPARATOR}{parent_source_version}"


def stamp_payload(
    payload: Mapping[str, Any],
    event_type: str,
    producer_version: str,
    parent: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a copy of ``payload`` with ``source_version`` and the ``parent`` reference.

    ``parent`` is the parent event as a mapping with ``event_id``, ``event_type`` and
    ``source_version`` (top level or in its payload). Only ``signal.created`` may lack one.
    """
    if event_type not in _POSITION:
        raise ValueError(f"not a chain event type: {event_type}")
    stamped = dict(payload)
    if parent is None:
        if event_type != CHAIN[0]:
            raise ValueError(f"{event_type} needs a parent event")
        stamped["source_version"] = build_source_version(producer_version)
        stamped["parent_event_id"] = None
        return stamped
    parent_version = _source_version_of(parent)
    stamped["source_version"] = build_source_version(producer_version, parent_version)
    stamped["parent_event_id"] = str(parent["event_id"])
    stamped["parent"] = {
        "event_id": str(parent["event_id"]),
        "event_type": str(parent["event_type"]),
        "source_version": parent_version,
    }
    return stamped


@dataclass(frozen=True)
class ChainTrace:
    """Outcome of ``trace_chain``: the ordered path found and the first gap, if any."""

    path: tuple[str, ...] = ()
    complete: bool = False
    gap: str | None = None
    problems: tuple[str, ...] = field(default_factory=tuple)


def trace_chain(events: Iterable[Mapping[str, Any]], max_events: int = 10_000) -> ChainTrace:
    """Walk from the last chain event back to ``signal.created`` and report the first gap.

    Complexity is O(n) in the events given, bounded by ``max_events`` and the chain length.
    """
    by_id: dict[str, Mapping[str, Any]] = {}
    for count, event in enumerate(events):
        if count >= max_events:
            return ChainTrace(gap="too many events", problems=("event limit exceeded",))
        if str(event.get("event_type")) in _POSITION:
            by_id[str(event["event_id"])] = event
    tails = [e for e in by_id.values() if e["event_type"] == CHAIN[-1]]
    if not tails:
        latest = max(by_id.values(), key=lambda e: _POSITION[e["event_type"]], default=None)
        if latest is None:
            return ChainTrace(gap=CHAIN[0], problems=("no chain events",))
        current: Mapping[str, Any] | None = latest
    else:
        current = tails[0]
    path: list[str] = []
    problems: list[str] = []
    for _ in range(len(CHAIN)):
        assert current is not None
        kind = str(current["event_type"])
        path.append(kind)
        if not _source_version_of(current):
            problems.append(f"{kind} has no source_version")
        if kind == CHAIN[0]:
            break
        parent = (current.get("payload") or {}).get("parent") or {}
        parent_event = by_id.get(str(parent.get("event_id")))
        expected = CHAIN[_POSITION[kind] - 1]
        if parent_event is None or parent_event["event_type"] != expected:
            problems.append(f"missing {expected} before {kind}")
            break
        current = parent_event
    path.reverse()
    complete = tuple(path) == CHAIN and not problems
    gap = None
    if not complete:
        missing = [name for name in CHAIN if name not in path]
        gap = missing[0] if missing else (problems[0] if problems else None)
        if path and path[0] != CHAIN[0]:
            gap = CHAIN[_POSITION[path[0]] - 1]
        elif path and path[-1] != CHAIN[-1]:
            gap = CHAIN[_POSITION[path[-1]] + 1]
    return ChainTrace(path=tuple(path), complete=complete, gap=gap, problems=tuple(problems))


def reference_parent(event_type: str, key: str, source_version: str = "") -> dict[str, Any]:
    """Return a parent mapping for a producer that runs apart from the previous chain step.

    The previous step may live in another service or transaction, so its event id is not at hand.
    The reference id is a deterministic UUIDv5 of ``event_type`` and ``key``: the same inputs give
    the same id, so a later audit can link the pieces. ``trace_chain`` reports the gap while the
    referenced event does not exist in the audited set. Pure function.
    """
    if event_type not in _POSITION:
        raise ValueError(f"not a chain event type: {event_type}")
    return {
        "event_id": str(uuid5(NAMESPACE_URL, f"invent-chain:{event_type}:{key}")),
        "event_type": event_type,
        "source_version": source_version,
    }


def chain_events_after(
    parent: Mapping[str, Any],
    steps: Sequence[tuple[str, Mapping[str, Any], str]],
    *,
    enabled: bool,
) -> list[dict[str, Any]]:
    """Return the outbox events (``event_id``, ``event_type``, ``payload``) that follow ``parent``.

    ``steps`` are ``(event_type, payload, producer_version)`` in chain order; each event names the
    previous one as its parent, so ``source_version`` grows by one segment per step and never past
    the chain length. With ``enabled`` false (``EVENT_CHAIN_EMIT`` off) nothing is returned, so
    the outbox is unchanged. Producers pass ``Settings.event_chain_emit``. Pure function.
    """
    if not enabled:
        return []
    events: list[dict[str, Any]] = []
    previous: Mapping[str, Any] = parent
    for event_type, payload, producer_version in steps:
        stamped = stamp_payload(payload, event_type, producer_version, previous)
        event = {"event_id": str(uuid4()), "event_type": event_type, "payload": stamped}
        events.append(event)
        previous = event
    return events
