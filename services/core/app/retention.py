"""Purge inactive companies, execute opt-outs, and reject personal identifiers.

``purge_inactive_companies`` deletes companies whose last signal (or creation, when they never had
one) is older than 12 months, with a dry-run mode, and is safe to call from a scheduler or from
``python -m services.core.app.retention``. ``opt_out`` deletes one company by id or NIT and is
idempotent: repeating it reports ``already_removed``. ``reject_personal_identifiers`` refuses
document numbers, private phones, and residential addresses, and ``ensure_public_role`` limits
stored persons to public business roles (Ley 1581/2012, REQ-CMP-001). Audit entries keep only
hashed identifiers. It relates to ``main.py``, ``repository.py``, and
``docs/compliance/processing-activities.md``. A purge deletes up to ``limit`` companies in one
transaction.
"""

import argparse
import asyncio
import hashlib
import json
import re
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from services.core.app.companies import normalize_nit

RETENTION_DAYS = 365
DEFAULT_PURGE_LIMIT = 500

FORBIDDEN_KEY_PARTS = (
    "cedula",
    "documentnumber",
    "documentonumero",
    "numerodocumento",
    "documentoidentidad",
    "idnumber",
    "passport",
    "pasaporte",
    "tarjetaidentidad",
    "birthdate",
    "fechanacimiento",
    "homeaddress",
    "residentialaddress",
    "direccionresidencia",
    "personalphone",
    "privatephone",
    "telefonoparticular",
    "celularpersonal",
    "healthdata",
    "sexualorientation",
)
DOCUMENT_VALUE = re.compile(
    r"(?i)\b(?:c\.?\s?c\.?|c[e\u00e9]dula(?:\s+de\s+ciudadan[i\u00ed]a)?)\b[\s:#.-]*\d[\d.\s]{5,}\d"
)
PUBLIC_ROLES = (
    "ceo",
    "cto",
    "cfo",
    "coo",
    "founder",
    "fundador",
    "gerente",
    "manager",
    "director",
    "presidente",
    "owner",
    "representante legal",
)


class PersonalDataError(ValueError):
    """The payload carries data this platform never stores."""


class RetentionTransaction(Protocol):
    async def find_purge_candidates(self, cutoff: datetime, limit: int) -> list[UUID]: ...
    async def find_company(self, company_id: UUID | None, nit: str | None) -> UUID | None: ...
    async def delete_company_data(self, company_id: UUID) -> dict[str, int]: ...
    async def add_audit(self, *, action: str, payload: dict[str, Any]) -> None: ...


class RetentionUnitOfWork(Protocol):
    async def __aenter__(self) -> RetentionTransaction: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...


class OptOutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: UUID | None = None
    nit: str | None = None
    requested_via: str = "legal_inbox"

    @model_validator(mode="after")
    def _needs_identifier(self) -> "OptOutRequest":
        if self.company_id is None and normalize_nit(self.nit) is None:
            raise ValueError("company_id or nit is required")
        return self


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def retention_cutoff(now: datetime) -> datetime:
    return now - timedelta(days=RETENTION_DAYS)


def _normalized_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def reject_personal_identifiers(data: Any, path: str = "") -> None:
    """Raise ``PersonalDataError`` naming the offending field (never its value)."""
    if isinstance(data, dict):
        for key, value in data.items():
            where = f"{path}.{key}" if path else str(key)
            normalized = _normalized_key(str(key))
            if any(part in normalized for part in FORBIDDEN_KEY_PARTS):
                raise PersonalDataError(f"personal identifier field rejected: {where}")
            reject_personal_identifiers(value, where)
    elif isinstance(data, list):
        for index, item in enumerate(data):
            reject_personal_identifiers(item, f"{path}[{index}]")
    elif isinstance(data, str) and DOCUMENT_VALUE.search(data):
        raise PersonalDataError(f"document number rejected in field: {path or 'value'}")


def ensure_public_role(role_title: str | None) -> str:
    """Return the role when it is a public business role, else raise ``PersonalDataError``."""
    folded = (role_title or "").strip().lower()
    if not any(role in folded for role in PUBLIC_ROLES):
        raise PersonalDataError("only public business roles may be stored")
    return folded


async def purge_inactive_companies(
    unit_of_work: RetentionUnitOfWork,
    *,
    now: datetime | None = None,
    dry_run: bool = False,
    limit: int = DEFAULT_PURGE_LIMIT,
) -> dict[str, Any]:
    """Delete (or, with ``dry_run``, only count) companies inactive for 12 months."""
    moment = now or datetime.now(UTC)
    cutoff = retention_cutoff(moment)
    deleted: dict[str, int] = {}
    async with unit_of_work as tx:
        candidates = await tx.find_purge_candidates(cutoff, limit)
        if not dry_run:
            for company_id in candidates:
                for table, count in (await tx.delete_company_data(company_id)).items():
                    deleted[table] = deleted.get(table, 0) + count
            if candidates:
                await tx.add_audit(
                    action="retention.purge",
                    payload={
                        "cutoff": cutoff.isoformat(),
                        "companies": len(candidates),
                        "deleted": deleted,
                    },
                )
    return {
        "cutoff": cutoff.isoformat(),
        "dry_run": dry_run,
        "candidates": len(candidates),
        "purged": 0 if dry_run else len(candidates),
        "deleted": deleted,
    }


async def opt_out(unit_of_work: RetentionUnitOfWork, request: OptOutRequest) -> dict[str, Any]:
    """Delete the company and its data; a repeat finds nothing and still succeeds."""
    nit = normalize_nit(request.nit)
    reference = str(request.company_id) if request.company_id else f"nit:{nit}"
    async with unit_of_work as tx:
        company_id = await tx.find_company(request.company_id, nit)
        if company_id is None:
            return {"status": "already_removed", "deleted": {}}
        deleted = await tx.delete_company_data(company_id)
        await tx.add_audit(
            action="company.opt_out",
            payload={
                "reference_sha256": _fingerprint(reference),
                "requested_via": request.requested_via,
                "deleted": deleted,
            },
        )
    return {"status": "removed", "deleted": deleted}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="retention", description="Purge inactive companies.")
    parser.add_argument("--dry-run", action="store_true", help="count candidates, delete nothing")
    parser.add_argument("--limit", type=int, default=DEFAULT_PURGE_LIMIT)
    return parser


async def run(argv: Sequence[str] | None = None) -> int:
    from libs.common.settings import get_settings
    from services.core.app.repository import CoreRepository

    args = build_parser().parse_args(argv)
    repository = CoreRepository(get_settings().database_url)
    try:
        result = await purge_inactive_companies(
            repository.retention_unit_of_work(), dry_run=args.dry_run, limit=args.limit
        )
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        await repository.close()
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
