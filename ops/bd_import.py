"""Import Bright Data company, job, and decision-maker data for the five target cities.

``make bd-import PHASE=<0|1|2|3|all> CITIES=...`` runs this script inside the Compose
network (runbook: ``docs/runbooks/bright-data-import.md``). Phases:

- 0, probe: one minimal call per endpoint (3 records or fewer), no landing. Redacted
  samples go to ``tests/fixtures/brightdata/recorded/`` and the field coverage is printed.
- 1: Google Maps discover by location (REST), LinkedIn jobs (official ``brightdata-sdk``),
  and Business Search companies (REST), run in parallel.
- 2: LinkedIn company profiles (SDK) for the unique company URLs of the landed jobs.
- 3: Business Search people (REST) for the best scored imported companies; every match
  becomes a ``decision_maker`` signal in core with an idempotency key.

Every raw result of phases 1 and 2 enters through ``IngestRepository.persist_snapshot``
(the poller's path), so the processor, resolution, core, ml, pulse, and brief run
unchanged; the dataset id of each snapshot routes it to its source mapping. Phase 3
already knows the company id, so it skips mapping and resolution and calls core's
internal API (INV-01). Only name, title, and the public profile of a person are kept;
e-mail and phone values are never read into the payload, stored, or logged (INV-12).

A hard budget (``BD_IMPORT_MAX_USD``, default 25, plus per-source caps
``BD_IMPORT_MAX_USD_<SOURCE>``) is checked before every paid call from the price table
(estimated cost = records requested times price). A ledger in ``BD_IMPORT_STATE_DIR``
makes re-runs skip calls that already landed, and ``--dry-run`` makes no call and writes
nothing. Secrets are never printed: the token goes straight to the clients.
"""

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import sys
from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

import httpx
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from libs.common.internal_auth import service_token_header  # noqa: E402
from libs.common.settings import Settings, get_settings  # noqa: E402
from libs.domain.cities import (  # noqa: E402
    BARRANQUILLA,
    BOGOTA,
    CALI,
    CANONICAL_CITIES,
    CARTAGENA,
    CITY_COORDINATES,
    MEDELLIN,
    fold,
    normalize_city,
)
from services.ingest.app.connectors import BrightDataConnector, ConnectorError  # noqa: E402
from services.ingest.app.mapping import MappingError, load_mapping, normalize_record  # noqa: E402
from services.ingest.app.repository import IngestRepository  # noqa: E402
from services.ingest.app.snapshot import payload_sha256  # noqa: E402
from services.ingest.app.source_config import (  # noqa: E402
    BUSINESS_SEARCH_COMPANIES_DATASET_ID,
    dataset_sources,
)
from services.ingest.app.tenant import load_ingest_tenant_id  # noqa: E402
from services.ingest.app.to_core import HttpCoreClient  # noqa: E402

SDK_VERSION = "2.5.2"
# Dataset ids. The LinkedIn ids are the ones brightdata-sdk 2.5.2 uses (checked at runtime).
MAPS_DATASET_ID = "gd_m8ebnr0q2qlklc02fz"
LINKEDIN_JOBS_DATASET_ID = "gd_lpfll7v5hcqtkxl6l"
LINKEDIN_COMPANIES_DATASET_ID = "gd_l1vikfnt1wgvvqz95w"
# Business Search has no dataset: a fixed id routes its landed snapshots (source_config).
BUSINESS_SEARCH_COMPANIES_ID = BUSINESS_SEARCH_COMPANIES_DATASET_ID
LANDED_DATASETS = {
    "maps": MAPS_DATASET_ID,
    "linkedin_jobs": LINKEDIN_JOBS_DATASET_ID,
    "linkedin_companies": LINKEDIN_COMPANIES_DATASET_ID,
    "business_search_companies": BUSINESS_SEARCH_COMPANIES_ID,
}
SOURCES = (*LANDED_DATASETS, "business_search_people")
PHASE_SOURCES = {
    1: ("maps", "linkedin_jobs", "business_search_companies"),
    2: ("linkedin_companies",),
    3: ("business_search_people",),
}

MAPS_KEYWORDS = (
    "empresa de software",
    "agencia de eventos",
    "catering",
    "sal\u00f3n de eventos",
    "hotel",
    "agencia de publicidad",
)
JOB_KEYWORDS = ("desarrollador", "software engineer", "marketing digital")
NICHES: dict[str, tuple[str, ...]] = {
    "software": ("software",),
    "events_hospitality": ("events", "hospitality"),
}
DECISION_TITLES = (
    "CEO",
    "Gerente General",
    "Fundador",
    "CTO",
    "Director Comercial",
    "Director de Mercadeo",
)
# Folded title words that prove a public business role (INV-12), Spanish and English.
DECISION_ROLE_WORDS = (
    "ceo",
    "chief executive officer",
    "gerente general",
    "general manager",
    "fundador",
    "fundadora",
    "cofundador",
    "cofundadora",
    "founder",
    "cofounder",
    "cto",
    "chief technology officer",
    "director comercial",
    "directora comercial",
    "commercial director",
    "sales director",
    "director de mercadeo",
    "directora de mercadeo",
    "director de marketing",
    "directora de marketing",
    "marketing director",
    "cmo",
    "chief marketing officer",
)
# A title with one of these words is not the role itself ("Assistant to the CEO").
NOT_DECISION_WORDS = ("assistant", "asistente", "former", "ex", "intern", "practicante")
# Fields of a LinkedIn company `employees` entry: the name, and the role when the dataset
# has one (the Phase 0 sample had only img, link, and title = the person's name).
EMPLOYEE_NAME_KEYS = ("title", "name")
EMPLOYEE_ROLE_KEYS = ("subtitle", "position", "headline", "job_title", "role")
JOB_LOCATIONS = {
    MEDELLIN: "Medell\u00edn, Antioquia, Colombia",
    BOGOTA: "Bogot\u00e1, D.C., Colombia",
    CALI: "Cali, Valle del Cauca, Colombia",
    BARRANQUILLA: "Barranquilla, Atl\u00e1ntico, Colombia",
    CARTAGENA: "Cartagena, Bol\u00edvar, Colombia",
}
# Spellings of the headquarters city that Business Search may hold.
SEARCH_CITY_NAMES = {
    MEDELLIN: ("Medell\u00edn", "Medellin"),
    BOGOTA: ("Bogot\u00e1", "Bogota"),
    CALI: ("Cali",),
    BARRANQUILLA: ("Barranquilla",),
    CARTAGENA: ("Cartagena", "Cartagena de Indias"),
}
COUNTRY = "CO"
MAPS_ZOOM = 13
MAPS_LIMIT = 40
JOBS_LIMIT = 50
COMPANY_SEARCH_LIMIT = 100
COMPANY_URL_CAP = 300
COMPANY_URL_BATCH = 50
TOP_COMPANIES = 150
PEOPLE_LIMIT = 10
PROBE_LIMIT = 3
MID_SIZE = (11, 500)
PEOPLE_VIEW = {"fields": ["name", "current_title", "current_company_name", "url"]}
CONCURRENCY = 5


@dataclass(frozen=True)
class Price:
    """Estimated price: ``usd`` per unit of ``records_per_unit`` requested records."""

    usd: float
    records_per_unit: int
    confirmed: bool
    basis: str

    def cost(self, records: int) -> float:
        if records <= 0:
            return 0.0
        return math.ceil(records / self.records_per_unit) * self.usd


_PAYG = (
    "Web Scraper API pay-as-you-go is $1.50/1K records (brightdata.com/pricing/web-scraper-api); "
    "no per-scraper rate is published, so $2.00/1K (the SDK 2.5.2 LinkedIn constant) is used"
)
_SEARCH = (
    "$2 per 1,000 searches, a search is a block of up to 100 requested results "
    "(docs.brightdata.com/products/business-search/pricing)"
)
PRICES = {
    "maps": Price(0.002, 1, False, _PAYG),
    "linkedin_jobs": Price(0.002, 1, False, _PAYG),
    "linkedin_companies": Price(0.002, 1, False, _PAYG),
    "business_search_companies": Price(0.002, 100, True, _SEARCH),
    "business_search_people": Price(0.002, 100, True, _SEARCH),
}
DEFAULT_MAX_USD = 25.0
PROBE_MAX_USD = 2.0
SOURCE_CAPS_USD = {
    "maps": 5.0,
    "linkedin_jobs": 4.0,
    "linkedin_companies": 2.0,
    "business_search_companies": 1.0,
    "business_search_people": 1.0,
}

# Keys whose whole value describes persons (job poster, employee lists, company posts with
# tagged people, reviewers); dropped before landing (INV-12). None of them is mapped.
PERSON_KEYS = frozenset(
    {
        "job_poster",
        "employees",
        "alumni",
        "alumni_information",
        "updates",
        "tagged_people",
        "reviews",
        "top_reviews",
        "reviewer",
        "reviewers",
        "author",
        "people",
        "person",
        "hiring_team",
        "recruiter",
    }
)
# Wider list used only to redact recorded fixtures (decision_makers holds persons too).
FIXTURE_PERSON_KEYS = PERSON_KEYS | {
    "posts",
    "owner",
    "user",
    "contacts",
    "email",
    "decision_makers",
}
PERSON_SOURCES = frozenset({"business_search_people"})
# E-mail addresses in any landed text (job descriptions) are masked before mapping (INV-12).
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
EMAIL_MASK = "[email removed]"


class BudgetExceededError(RuntimeError):
    """A paid call would go beyond the run or source cap; nothing was spent."""


class ImportCallError(RuntimeError):
    """A provider call failed; the message carries no secret."""


class AccessDeniedError(ImportCallError):
    """The account may not use this endpoint (HTTP 401 or 403); the call is not billed."""


@dataclass
class Budget:
    """Hard caps on the estimated spend of one run; ``reserve`` runs before each call."""

    max_usd: float
    caps: dict[str, float]
    spent: dict[str, float] = field(default_factory=dict)

    @property
    def total(self) -> float:
        return sum(self.spent.values())

    def reserve(self, source: str, records: int) -> float:
        """Book the estimate of ``records`` or raise ``BudgetExceededError``."""
        cost = PRICES[source].cost(records)
        used = self.spent.get(source, 0.0)
        if self.total + cost > self.max_usd + 1e-9:
            raise BudgetExceededError(
                f"{source}: ${cost:.4f} would pass the run cap ${self.max_usd:.2f} "
                f"(estimated so far ${self.total:.4f})"
            )
        cap = self.caps.get(source, 0.0)
        if used + cost > cap + 1e-9:
            raise BudgetExceededError(
                f"{source}: ${cost:.4f} would pass the source cap ${cap:.2f} "
                f"(source estimate ${used:.4f})"
            )
        self.spent[source] = used + cost
        return cost

    def release(self, source: str, reserved: float) -> None:
        """Give back a reservation for a call that was refused and not billed."""
        self.spent[source] = max(0.0, self.spent.get(source, 0.0) - reserved)

    def settle(self, source: str, reserved: float, delivered: int) -> None:
        """Add the excess when the provider delivered more than requested."""
        actual = PRICES[source].cost(delivered)
        if actual > reserved:
            self.spent[source] = self.spent.get(source, 0.0) + actual - reserved
            print(f"  warning: {source} delivered more than requested (+${actual - reserved:.4f})")


def budget_from_env(environ: Mapping[str, str], *, max_usd: float | None, phase: str) -> Budget:
    """Read ``BD_IMPORT_MAX_USD`` and ``BD_IMPORT_MAX_USD_<SOURCE>``; the probe stays below $2."""
    total = max_usd if max_usd is not None else float(environ.get("BD_IMPORT_MAX_USD", "25"))
    if phase == "0":
        total = min(total, PROBE_MAX_USD)
    caps = {
        source: float(environ.get(f"BD_IMPORT_MAX_USD_{source.upper()}", str(cap)))
        for source, cap in SOURCE_CAPS_USD.items()
    }
    return Budget(max_usd=total, caps=caps)


def call_key(source: str, request: Any) -> str:
    """Return the stable key of one paid call (source plus its exact request)."""
    material = json.dumps({"source": source, "request": request}, sort_keys=True, default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


class Ledger:
    """Calls already done (JSON file); ``path=None`` keeps it in memory."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.entries: dict[str, dict[str, Any]] = {}
        if path is not None and path.is_file():
            self.entries = json.loads(path.read_text(encoding="utf-8"))

    def done(self, key: str) -> bool:
        return key in self.entries

    def record(self, key: str, entry: dict[str, Any]) -> None:
        self.entries[key] = entry
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.entries, indent=1, sort_keys=True), encoding="utf-8")
        temporary.replace(self.path)


# ---------------------------------------------------------------- request builders


def maps_inputs(city: str, keywords: Sequence[str]) -> list[dict[str, Any]]:
    latitude, longitude = CITY_COORDINATES[city]
    return [
        {
            "country": COUNTRY,
            "lat": latitude,
            "long": longitude,
            "zoom_level": MAPS_ZOOM,
            "keyword": k,
        }
        for k in keywords
    ]


def maps_params(limit: int) -> dict[str, str]:
    return {"type": "discover_new", "discover_by": "location", "limit_per_input": str(limit)}


def jobs_inputs(city: str, keywords: Sequence[str]) -> list[dict[str, Any]]:
    return [{"keyword": k, "location": JOB_LOCATIONS[city], "country": COUNTRY} for k in keywords]


def company_search_body(city: str, niche: str, limit: int) -> dict[str, Any]:
    """Ludicrous Business Search: Colombian companies of one city, niche, 11-500 employees."""
    low, high = MID_SIZE
    return {
        "mode": "ludicrous",
        "query": {
            "and": [
                {"equals": {"headquarters_country_code": COUNTRY}},
                {"range": {"employees_in_linkedin": {">=": low, "<=": high}}},
                {"or": [{"equals": {"headquarters_city": n}} for n in SEARCH_CITY_NAMES[city]]},
                {"or": [{"text": {"industry": term}} for term in NICHES[niche]]},
            ]
        },
        "limit": limit,
        "view": "summary",
    }


def people_search_body(company_name: str, limit: int) -> dict[str, Any]:
    """Ludicrous Business Search: decision makers of one company in Colombia."""
    titles = [{"text": {"current_title": {"value": t, "mode": "all"}}} for t in DECISION_TITLES]
    return {
        "mode": "ludicrous",
        "query": {
            "and": [
                {"or": titles},
                {"text": {"current_company_name": {"value": company_name, "mode": "all"}}},
                {"equals": {"country_code": COUNTRY}},
            ]
        },
        "limit": limit,
        "view": PEOPLE_VIEW,
    }


# ---------------------------------------------------------------- record helpers


def drop_person_keys(record: Any, keys: frozenset[str] = PERSON_KEYS) -> Any:
    """Return ``record`` without the values of person keys, at any depth."""
    if isinstance(record, dict):
        return {k: drop_person_keys(v, keys) for k, v in record.items() if k.lower() not in keys}
    if isinstance(record, list):
        return [drop_person_keys(v, keys) for v in record]
    return record


def placeholder(value: Any) -> Any:
    """Replace every value with a neutral one of the same type; keys stay."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return 0
    if isinstance(value, float):
        return 0.0
    if isinstance(value, str):
        return "https://example.invalid/redacted" if value.startswith("http") else "redacted"
    if isinstance(value, list):
        return [placeholder(v) for v in value[:1]]
    if isinstance(value, dict):
        return {k: placeholder(v) for k, v in value.items()}
    return "redacted"


def redact_fixture(record: Any, *, person: bool) -> Any:
    """Redact a recorded sample: persons become placeholders, company data stays."""
    if person:
        return placeholder(record)
    if isinstance(record, dict):
        return {
            k: placeholder(v)
            if k.lower() in FIXTURE_PERSON_KEYS
            else redact_fixture(v, person=False)
            for k, v in record.items()
        }
    if isinstance(record, list):
        return [redact_fixture(v, person=False) for v in record]
    return mask_emails(record)


def mask_emails(value: Any) -> Any:
    """Replace every e-mail address inside ``value`` (any depth) with a fixed mask."""
    if isinstance(value, str):
        return EMAIL.sub(EMAIL_MASK, value) if "@" in value else value
    if isinstance(value, list):
        return [mask_emails(v) for v in value]
    if isinstance(value, dict):
        return {k: mask_emails(v) for k, v in value.items()}
    return value


def is_error_record(record: dict[str, Any]) -> bool:
    return bool(record.get("error") or record.get("error_code"))


def field_coverage(records: Sequence[dict[str, Any]]) -> dict[str, int]:
    """Count the records with a non-empty value per dotted field path (keys only)."""

    def leaves(value: dict[str, Any], prefix: str) -> Iterator[tuple[str, Any]]:
        for key, item in value.items():
            path = f"{prefix}{key}"
            if isinstance(item, dict) and item:
                yield from leaves(item, f"{path}.")
            else:
                yield path, item

    counts: dict[str, int] = {}
    for record in records:
        for path, item in leaves(record, ""):
            counts[path] = counts.get(path, 0) + int(item not in (None, "", [], {}))
    return counts


def company_url(value: Any) -> str | None:
    """Return a LinkedIn company URL on www, without query or trailing slash, else None."""
    if not isinstance(value, str) or "linkedin.com/company/" not in value:
        return None
    slug = value.split("?")[0].rstrip("/").split("linkedin.com/company/", 1)[1]
    return f"https://www.linkedin.com/company/{slug}" if slug else None


def normalized_fields(source: str, record: dict[str, Any]) -> dict[str, Any] | None:
    """Return the mapped fields of a landed record, or None when it fails mapping."""
    try:
        return normalize_record(load_mapping(source), record).fields
    except MappingError:
        return None


def is_decision_title(title: str) -> bool:
    folded = f" {fold(title)} "
    if any(f" {word} " in folded for word in NOT_DECISION_WORDS):
        return False
    return any(f" {word} " in folded for word in DECISION_ROLE_WORDS)


def profile_url(value: Any) -> str | None:
    """Return a public LinkedIn profile URL on www without query, else None."""
    if not isinstance(value, str) or "linkedin.com/in/" not in value:
        return None
    slug = value.split("?")[0].rstrip("/").split("linkedin.com/in/", 1)[1]
    return f"https://www.linkedin.com/in/{slug}"[:300] if slug else None


def employee_decision_makers(employees: Any) -> list[dict[str, str]]:
    """Keep name, role, and public profile of the decision makers of a company's
    ``employees`` list; every other entry and field is dropped (INV-12)."""
    found: list[dict[str, str]] = []
    for entry in employees if isinstance(employees, list) else []:
        if not isinstance(entry, dict):
            continue
        name = next((entry[k] for k in EMPLOYEE_NAME_KEYS if isinstance(entry.get(k), str)), "")
        role = next((entry[k] for k in EMPLOYEE_ROLE_KEYS if isinstance(entry.get(k), str)), "")
        url = profile_url(entry.get("link") or entry.get("url"))
        if name.strip() and url and is_decision_title(role):
            person = {"name": name.strip()[:200], "title": role.strip()[:200], "profile_url": url}
            if all(p["profile_url"] != url for p in found):
                found.append(person)
    return found


def with_decision_makers(record: dict[str, Any]) -> dict[str, Any]:
    """Return a company profile whose ``employees`` list became ``decision_makers``."""
    if "employees" not in record:
        return record
    return {**record, "decision_makers": employee_decision_makers(record["employees"])}


def prepare_record(source: str, record: dict[str, Any]) -> dict[str, Any]:
    """Shape one provider record before it is landed or read (INV-12).

    Company profiles keep only the decision makers of their employee list; person lists
    are dropped; e-mail addresses in any text are masked, so none reaches mapping or core.
    """
    if source == "linkedin_companies":
        record = with_decision_makers(record)
    prepared: dict[str, Any] = mask_emails(drop_person_keys(record))
    return prepared


def decision_makers(documents: Iterable[Any]) -> list[dict[str, str]]:
    """Keep only name, title, and public profile of matching people (INV-12)."""
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for document in documents:
        if not isinstance(document, dict) or not isinstance(document.get("data"), dict):
            continue
        data = document["data"]
        name, title, url = data.get("name"), data.get("current_title"), data.get("url")
        if not isinstance(name, str) or not isinstance(title, str):
            continue
        if not name.strip() or not is_decision_title(title):
            continue
        person: dict[str, str] = {"name": name.strip()[:200], "title": title.strip()[:200]}
        if public := profile_url(url):
            person["profile_url"] = public
        elif isinstance(document.get("bright_id"), str):
            person["public_profile_id"] = str(document["bright_id"])[:120]
        else:
            continue
        identity = person.get("profile_url") or person["public_profile_id"]
        if identity not in seen:
            seen.add(identity)
            found.append(person)
    return found


def decision_maker_signal(
    company_id: str,
    person: Mapping[str, str],
    tenant_id: UUID,
    found_at: datetime,
    source: str = "business_search_people",
) -> tuple[dict[str, Any], str]:
    """Return the core signal body of one decision maker and its idempotency key."""
    identity = person.get("profile_url") or person.get("public_profile_id") or person["name"]
    key = hashlib.sha256(
        "\x1f".join((str(tenant_id), "decision_maker", company_id, identity)).encode("utf-8")
    ).hexdigest()
    payload = {
        **{
            k: person[k]
            for k in ("name", "title", "profile_url", "public_profile_id")
            if k in person
        },
        "source": source,
        "found_at": found_at.isoformat(),
    }
    body = {
        "company_id": company_id,
        "source": source,
        "signal_type": "decision_maker",
        "observed_at": found_at.isoformat(),
        "payload": payload,
        "source_external_id": f"dm-{key[:40]}",
        "tenant_id": str(tenant_id),
    }
    return body, key


def parse_cities(text: str | None) -> list[str]:
    """Return canonical cities from a comma list (any spelling); all five when empty."""
    if not text or not text.strip() or text.strip().lower() == "all":
        return list(CANONICAL_CITIES)
    cities: list[str] = []
    for part in text.split(","):
        city = normalize_city(part)
        if city is None:
            raise SystemExit(
                f"unknown city {part.strip()!r}; use one of {', '.join(CANONICAL_CITIES)}"
            )
        if city not in cities:
            cities.append(city)
    return cities


def routing_problems(settings: Settings, sources: Iterable[str]) -> list[str]:
    """Name the ``.env`` lines the ingest processor needs to route each landed dataset."""
    routed = dataset_sources(settings)
    problems = []
    for source in sources:
        dataset_id = LANDED_DATASETS.get(source)
        if dataset_id and routed.get(dataset_id) != source:
            name = f"SOURCE_{source.upper()}"
            problems.append(f"{name}_ENABLED=true and {name}_DATASET_ID={dataset_id}")
    return problems


# ---------------------------------------------------------------- statistics


@dataclass
class SourceStats:
    """Counters for the end-of-run report of one source."""

    calls: int = 0
    skipped: int = 0
    records: int = 0
    errors: int = 0
    with_contact: int = 0
    with_size: int = 0
    mid_size: int = 0
    companies_searched: int = 0
    companies_with_dm: int = 0
    signals: int = 0
    failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add_records(self, source: str, records: Sequence[dict[str, Any]]) -> None:
        low, high = MID_SIZE
        self.records += len(records)
        for record in records:
            fields = normalized_fields(source, record) or {}
            if fields.get("phone") or fields.get("website"):
                self.with_contact += 1
            count = fields.get("employee_count")
            if isinstance(count, int):
                self.with_size += 1
                self.mid_size += int(low <= count <= high)


def _percent(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f}%" if whole else "n/a"


def print_report(stats: Mapping[str, SourceStats], budget: Budget) -> None:
    print("\nsource                     calls skip records phone|web mid-size  with-DM  est USD")
    for source in (*SOURCES, "decision_makers"):
        item = stats.get(source)
        if item is None:
            continue
        mid = _percent(item.mid_size, item.with_size)
        dm = _percent(item.companies_with_dm, item.companies_searched)
        contact = _percent(item.with_contact, item.records) if source in LANDED_DATASETS else "n/a"
        print(
            f"{source:<26} {item.calls:>5} {item.skipped:>4} {item.records:>7} {contact:>9} "
            f"{mid:>8} {dm:>8} {budget.spent.get(source, 0.0):>8.4f}"
        )
        if item.errors:
            print(f"  {item.errors} provider error records were not landed")
        if item.signals:
            print(f"  {item.signals} decision_maker signals sent to core")
        for note in item.notes:
            print(f"  note: {note}")
        for failure in item.failures:
            print(f"  failed: {failure}")
    print(f"estimated total ${budget.total:.4f} (run cap ${budget.max_usd:.2f})")


# ---------------------------------------------------------------- provider adapters


@dataclass(frozen=True)
class CallResult:
    """Records of one provider call and the provider id that names them."""

    provider_id: str
    records: list[dict[str, Any]]
    raw: list[dict[str, Any]] = field(default_factory=list)


def _sdk_records(result: Any) -> tuple[str | None, list[dict[str, Any]], str | None, bool]:
    """Flatten an SDK ``ScrapeResult`` or list of them: id, records, error, timed out."""
    results = result if isinstance(result, list) else [result]
    snapshot_id: str | None = None
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    timed_out = False
    for item in results:
        snapshot_id = snapshot_id or getattr(item, "snapshot_id", None)
        data = getattr(item, "data", None)
        if isinstance(data, list):
            records.extend(r for r in data if isinstance(r, dict))
        elif isinstance(data, dict):
            records.append(data)
        if not getattr(item, "success", False):
            errors.append(str(getattr(item, "error", "") or "unknown error")[:300])
            timed_out = timed_out or getattr(item, "status", "") == "timeout"
    return snapshot_id, records, ("; ".join(errors) or None), timed_out


class LinkedInSdk:
    """LinkedIn calls through ``brightdata-sdk`` (async ``BrightDataClient``).

    ``client.search.linkedin.jobs`` has no limit argument in SDK 2.5.2, so the jobs
    search runs the same dataset and discovery parameters through the scraper's own
    ``workflow_executor`` with ``limit_per_input``; without it the budget cap could not
    hold. Company profiles use ``client.scrape.linkedin.companies(url=...)`` as is.
    """

    def __init__(self, client: Any, rest: "BrightDataRest") -> None:
        self._client = client
        self._rest = rest
        search, scrape = client.search.linkedin, client.scrape.linkedin
        if search.DATASET_ID_JOBS != LINKEDIN_JOBS_DATASET_ID or (
            scrape.DATASET_ID_COMPANIES != LINKEDIN_COMPANIES_DATASET_ID
        ):
            raise ImportCallError("brightdata-sdk dataset ids changed; update ops/bd_import.py")

    async def _finish(self, result: Any, source: str, dataset_id: str) -> CallResult:
        snapshot_id, records, error, timed_out = _sdk_records(result)
        if timed_out and snapshot_id:
            # The SDK stopped polling; the Datasets API still holds the snapshot.
            records = await self._rest.wait_and_fetch(source, dataset_id, snapshot_id)
        elif error and not records:
            raise ImportCallError(f"{source}: {error}")
        return CallResult(snapshot_id or "unknown", records)

    async def jobs(self, inputs: list[dict[str, Any]], limit: int, timeout: int) -> CallResult:
        scraper = self._client.search.linkedin
        result = await scraper.workflow_executor.execute(
            payload=inputs,
            dataset_id=scraper.DATASET_ID_JOBS,
            poll_timeout=timeout,
            include_errors=True,
            sdk_function="jobs",
            extra_params={
                "type": "discover_new",
                "discover_by": "keyword",
                "limit_per_input": str(limit),
            },
            limit_per_input=limit,
        )
        return await self._finish(result, "linkedin_jobs", LINKEDIN_JOBS_DATASET_ID)

    async def companies(self, urls: list[str], timeout: int) -> CallResult:
        result = await self._client.scrape.linkedin.companies(url=urls, timeout=timeout)
        return await self._finish(result, "linkedin_companies", LINKEDIN_COMPANIES_DATASET_ID)


def open_sdk_client(token: str) -> Any:
    """Create the SDK client (imported lazily: tests and dry runs never need it)."""
    try:
        from brightdata import BrightDataClient
    except ImportError as exc:  # pragma: no cover - depends on the optional extra
        raise SystemExit(
            f"brightdata-sdk is missing: pip install 'brightdata-sdk=={SDK_VERSION}' "
            "or use make bd-import"
        ) from exc
    return BrightDataClient(token=token, auto_create_zones=False, timeout=60)


class BrightDataRest:
    """Direct REST calls through the ingest ``BrightDataConnector`` (sync, run in threads)."""

    def __init__(
        self,
        client: httpx.Client,
        token: str,
        settings: Settings,
        *,
        timeout_seconds: float = 1800.0,
        poll_seconds: float = 10.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = client
        self._token = token
        self._settings = settings
        self.timeout_seconds = timeout_seconds
        self.poll_seconds = poll_seconds
        self._sleep = sleep

    def connector(
        self, source: str, dataset_id: str, extra_params: Mapping[str, str] | None = None
    ) -> BrightDataConnector:
        return BrightDataConnector(
            source,
            self._client,
            dataset_id=dataset_id,
            api_token=self._token,
            retry_attempts=max(3, self._settings.upstream_retry_attempts),
            retry_base_delay_seconds=1.0,
            retry_max_delay_seconds=8.0,
            extra_params=extra_params,
        )

    async def discover(
        self, source: str, dataset_id: str, inputs: list[dict[str, Any]], params: Mapping[str, str]
    ) -> CallResult:
        connector = self.connector(source, dataset_id, params)
        try:
            triggered = await asyncio.to_thread(connector.trigger, inputs)
        except ConnectorError as exc:
            raise ImportCallError(f"{source} trigger: {exc}") from exc
        records = await self.wait_and_fetch(source, dataset_id, triggered.snapshot_id)
        return CallResult(triggered.snapshot_id, records)

    async def wait_and_fetch(
        self, source: str, dataset_id: str, snapshot_id: str
    ) -> list[dict[str, Any]]:
        connector = self.connector(source, dataset_id)
        waited = 0.0
        while True:
            try:
                progress = await asyncio.to_thread(connector.progress, snapshot_id)
                if progress.status == "failed":
                    raise ImportCallError(f"{source} snapshot {snapshot_id} failed")
                if progress.status == "ready":
                    return await asyncio.to_thread(connector.fetch, snapshot_id)
            except ConnectorError as exc:
                if not exc.retryable:
                    raise ImportCallError(f"{source} snapshot {snapshot_id}: {exc}") from exc
            if waited >= self.timeout_seconds:
                raise ImportCallError(f"{source} snapshot {snapshot_id} not ready in time")
            await self._sleep(self.poll_seconds)
            waited += self.poll_seconds

    async def search(self, entity: str, body: dict[str, Any]) -> dict[str, Any]:
        source = "business_search_companies"
        connector = self.connector(source, BUSINESS_SEARCH_COMPANIES_ID)
        try:
            answer = await asyncio.to_thread(connector.post_json, f"/search/{entity}", body)
        except ConnectorError as exc:
            if exc.status_code in (401, 403):
                raise AccessDeniedError(f"business search {entity}: {exc}") from exc
            raise ImportCallError(f"business search {entity}: {exc}") from exc
        if not isinstance(answer, dict) or not isinstance(answer.get("documents"), list):
            raise ImportCallError(f"business search {entity}: unexpected answer")
        return answer


# ---------------------------------------------------------------- landing and core


class Landing(Protocol):
    """Where raw results go: the ingest repository, or nowhere in a dry run."""

    async def land(self, source: str, provider_id: str, records: list[dict[str, Any]]) -> None: ...

    async def landed_records(self, source: str) -> list[dict[str, Any]]: ...


class RepositoryLanding:
    """Land through ``IngestRepository.persist_snapshot``, the poller's path."""

    def __init__(self, repository: Any, clock: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self.repository = repository
        self.clock = clock

    async def land(self, source: str, provider_id: str, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        await self.repository.persist_snapshot(
            snapshot_id=provider_id,
            dataset_id=LANDED_DATASETS[source],
            payload=records,
            payload_sha256=payload_sha256(records),
            fetched_at=self.clock(),
        )

    async def landed_records(self, source: str) -> list[dict[str, Any]]:
        async with self.repository.engine.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT payload FROM ingest.raw_snapshot "
                    "WHERE source = 'bright_data' AND dataset_id = :dataset_id"
                ),
                {"dataset_id": LANDED_DATASETS[source]},
            )
            payloads = [row[0] for row in result.all()]
        records: list[dict[str, Any]] = []
        for payload in payloads:
            data = json.loads(payload) if isinstance(payload, str) else payload
            items = data if isinstance(data, list) else [data]
            records.extend(r for r in items if isinstance(r, dict))
        return records


class NoLanding:
    """Dry runs and the probe write nothing."""

    async def land(self, source: str, provider_id: str, records: list[dict[str, Any]]) -> None:
        return None

    async def landed_records(self, source: str) -> list[dict[str, Any]]:
        return []


@dataclass(frozen=True)
class ScoredCompany:
    company_id: str
    name: str
    score: int


class Core(Protocol):
    """What phase 3 needs from core's internal API."""

    async def scored_companies(self) -> list[ScoredCompany]: ...

    async def create_signal(
        self, body: dict[str, Any], *, idempotency_key: str
    ) -> dict[str, Any]: ...


class HttpCore:
    """Core's internal API: leads read as the local ``verify_live`` caller, signals as ingest.

    ``/internal/core/leads`` accepts only the gateway and ``verify_live`` (a caller that
    exists outside production only); writes reuse ingest's ``HttpCoreClient``.
    """

    READ_CALLER = "verify_live"
    PAGE = 200
    MAX_PAGES = 100

    def __init__(self, settings: Settings, tenant_id: UUID, client: httpx.AsyncClient) -> None:
        """``client`` must have core's URL as ``base_url`` (paths are relative)."""
        self._settings = settings
        self._tenant_id = tenant_id
        self._client = client
        self._writer = HttpCoreClient(
            str(client.base_url), settings.internal_service_token, client=client
        )

    async def scored_companies(self) -> list[ScoredCompany]:
        best: dict[str, ScoredCompany] = {}
        cursor: str | None = None
        for _page in range(self.MAX_PAGES):
            params: dict[str, Any] = {"limit": self.PAGE}
            if cursor:
                params["cursor"] = cursor
            headers = {
                **service_token_header(self.READ_CALLER, self._settings.internal_service_token),
                "X-Tenant-Id": str(self._tenant_id),
            }
            response = await self._client.get(
                "/internal/core/leads", params=params, headers=headers
            )
            response.raise_for_status()
            body = response.json()
            for row in body.get("data", []):
                score, name = row.get("score"), row.get("legal_name")
                if not isinstance(score, int) or not isinstance(name, str):
                    continue
                company = ScoredCompany(str(row["company_id"]), name, score)
                if company.company_id not in best or best[company.company_id].score < score:
                    best[company.company_id] = company
            cursor = body.get("next_cursor")
            if not cursor:
                break
        return sorted(best.values(), key=lambda c: (-c.score, c.name))

    async def create_signal(self, body: dict[str, Any], *, idempotency_key: str) -> dict[str, Any]:
        return await self._writer.create_signal(body, idempotency_key=idempotency_key)


# ---------------------------------------------------------------- the run


@dataclass
class Run:
    """State shared by the phases of one invocation."""

    budget: Budget
    ledger: Ledger
    landing: Landing
    cities: list[str]
    dry_run: bool
    rest: BrightDataRest | None = None
    sdk: LinkedInSdk | None = None
    core: Core | None = None
    tenant_id: UUID | None = None
    timeout_seconds: int = 1800
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    stats: dict[str, SourceStats] = field(default_factory=dict)
    # Sources whose endpoint answered 401/403: their remaining calls are skipped.
    blocked: set[str] = field(default_factory=set)

    def stat(self, source: str) -> SourceStats:
        return self.stats.setdefault(source, SourceStats())

    async def paid_call(
        self,
        source: str,
        request: Any,
        records_requested: int,
        call: Callable[[], Awaitable[CallResult]],
        *,
        billed_records: int | None = None,
    ) -> CallResult | None:
        """Reserve the budget, run one call, land its records; None when skipped.

        ``billed_records`` is the billed size when it does not depend on the answer
        (a Business Search block); otherwise the delivered records are billed.
        """
        stats = self.stat(source)
        key = call_key(source, request)
        if self.ledger.done(key) or source in self.blocked:
            stats.skipped += 1
            return None
        reserved = self.budget.reserve(source, records_requested)
        stats.calls += 1
        label = "plan" if self.dry_run else "call"
        print(
            f"  {label} {source}: {records_requested} records requested, ${reserved:.4f}; "
            f"estimated total ${self.budget.total:.4f}"
        )
        if self.dry_run:
            return None
        try:
            result = await call()
        except AccessDeniedError:
            # Not billed: give the reservation back and skip the rest of this source.
            self.budget.release(source, reserved)
            stats.calls -= 1
            stats.skipped += 1
            if source not in self.blocked:
                self.blocked.add(source)
                stats.notes.append("no access (HTTP 401/403): calls skipped, nothing billed")
            return None
        records = [prepare_record(source, r) for r in result.records if not is_error_record(r)]
        stats.errors += len(result.records) - len(records)
        self.budget.settle(source, reserved, billed_records or len(records))
        if source in LANDED_DATASETS:
            await self.landing.land(source, result.provider_id, records)
            stats.add_records(source, records)
        self.ledger.record(
            key,
            {
                "source": source,
                "provider_id": result.provider_id,
                "records": len(records),
                "at": self.clock().isoformat(),
            },
        )
        return CallResult(result.provider_id, records, raw=result.records)

    async def gather(self, source: str, calls: Iterable[Awaitable[Any]]) -> None:
        """Run calls concurrently; a provider failure or a budget stop is reported."""
        for outcome in await asyncio.gather(*calls, return_exceptions=True):
            if isinstance(outcome, BudgetExceededError | ImportCallError | httpx.HTTPError):
                self.stat(source).failures.append(f"{type(outcome).__name__}: {outcome}"[:400])
            elif isinstance(outcome, BaseException):
                raise outcome

    # ------------------------------------------------------------ phase 1

    def maps_call(self, city: str, keywords: Sequence[str], limit: int) -> Awaitable[Any]:
        inputs, params = maps_inputs(city, keywords), maps_params(limit)
        return self.paid_call(
            "maps",
            {"inputs": inputs, "params": params},
            len(inputs) * limit,
            lambda: self._rest().discover("maps", MAPS_DATASET_ID, inputs, params),
        )

    def jobs_call(self, city: str, keywords: Sequence[str], limit: int) -> Awaitable[Any]:
        inputs = jobs_inputs(city, keywords)
        return self.paid_call(
            "linkedin_jobs",
            {"inputs": inputs, "limit": limit},
            len(inputs) * limit,
            lambda: self._sdk().jobs(inputs, limit, self.timeout_seconds),
        )

    def company_search_call(self, city: str, niche: str, limit: int) -> Awaitable[Any]:
        body = company_search_body(city, niche, limit)
        return self.paid_call(
            "business_search_companies",
            body,
            limit,
            lambda: self._search("company", body),
            billed_records=limit,
        )

    async def _search(self, entity: str, body: dict[str, Any]) -> CallResult:
        answer = await self._rest().search(entity, body)
        provider_id = f"bs_{entity}_{call_key(entity, body)}"
        return CallResult(provider_id, [d for d in answer["documents"] if isinstance(d, dict)])

    async def phase1(self) -> None:
        await asyncio.gather(
            self.gather(
                "maps", (self.maps_call(c, MAPS_KEYWORDS, MAPS_LIMIT) for c in self.cities)
            ),
            self.gather(
                "linkedin_jobs", (self.jobs_call(c, JOB_KEYWORDS, JOBS_LIMIT) for c in self.cities)
            ),
            self.gather(
                "business_search_companies",
                (
                    self.company_search_call(c, niche, COMPANY_SEARCH_LIMIT)
                    for c in self.cities
                    for niche in NICHES
                ),
            ),
        )

    # ------------------------------------------------------------ phase 2

    async def company_urls(self) -> list[str]:
        """Unique LinkedIn company URLs of the landed jobs not scraped yet, capped."""
        done = set()
        for record in await self.landing.landed_records("linkedin_companies"):
            for value in (record.get("url"), (record.get("input") or {}).get("url")):
                if url := company_url(value):
                    done.add(url.lower())
        urls: list[str] = []
        for record in await self.landing.landed_records("linkedin_jobs"):
            url = company_url(record.get("company_url"))
            if url and url not in urls and url.lower() not in done:
                urls.append(url)
        return urls[:COMPANY_URL_CAP]

    def companies_call(self, urls: list[str]) -> Awaitable[Any]:
        return self.paid_call(
            "linkedin_companies",
            {"urls": urls},
            len(urls),
            lambda: self._sdk().companies(urls, self.timeout_seconds),
        )

    async def phase2(self) -> None:
        if self.dry_run:
            # A dry run reads nothing; plan for the cap.
            urls = [f"planned-{index}" for index in range(COMPANY_URL_CAP)]
        else:
            urls = await self.company_urls()
        print(f"  linkedin_companies: {len(urls)} company URLs")
        batches = [urls[i : i + COMPANY_URL_BATCH] for i in range(0, len(urls), COMPANY_URL_BATCH)]
        await self.gather("linkedin_companies", (self.companies_call(b) for b in batches))

    # ------------------------------------------------------------ phase 3

    async def imported_names(self) -> set[str]:
        """Folded names of every company this importer landed (never demo companies)."""
        names: set[str] = set()
        for source in LANDED_DATASETS:
            for record in await self.landing.landed_records(source):
                fields = normalized_fields(source, record) or {}
                for key in ("name", "company_name"):
                    if isinstance(fields.get(key), str):
                        names.add(fold(fields[key]))
        return names

    async def top_companies(self) -> list[ScoredCompany]:
        if self.dry_run:
            return [ScoredCompany(f"planned-{i}", f"planned {i}", 0) for i in range(TOP_COMPANIES)]
        names = await self.imported_names()
        scored = await self._core().scored_companies()
        return [c for c in scored if fold(c.name) in names][:TOP_COMPANIES]

    async def profile_decision_makers(self) -> dict[str, list[dict[str, str]]]:
        """Decision makers kept from the landed company profiles, by folded company name."""
        found: dict[str, list[dict[str, str]]] = {}
        for record in await self.landing.landed_records("linkedin_companies"):
            name = (normalized_fields("linkedin_companies", record) or {}).get("name")
            for person in record.get("decision_makers") or []:
                if not isinstance(person, dict) or not isinstance(name, str):
                    continue
                kept = {
                    k: str(person[k]) for k in ("name", "title", "profile_url") if person.get(k)
                }
                if len(kept) == 3 and is_decision_title(kept["title"]):
                    found.setdefault(fold(name), []).append(kept)
        return found

    async def search_people(
        self, company: ScoredCompany, semaphore: asyncio.Semaphore
    ) -> list[dict[str, str]]:
        """Business Search people for one company; empty when skipped or refused (403)."""
        source = "business_search_people"
        body = people_search_body(company.name, PEOPLE_LIMIT)
        async with semaphore:
            result = await self.paid_call(
                source,
                body,
                PEOPLE_LIMIT,
                lambda: self._search("people", body),
                billed_records=PEOPLE_LIMIT,
            )
        if result is None:
            return []
        stats = self.stat(source)
        people = decision_makers(result.records)
        stats.companies_searched += 1
        stats.companies_with_dm += int(bool(people))
        return people

    async def send_decision_maker(
        self, company: ScoredCompany, person: Mapping[str, str], source: str
    ) -> None:
        tenant_id = self.tenant_id
        if tenant_id is None:
            raise ImportCallError("INGEST_TENANT_ID is required for phase 3")
        signal, key = decision_maker_signal(
            company.company_id, person, tenant_id, self.clock(), source=source
        )
        await self._core().create_signal(signal, idempotency_key=key)
        self.stat(source).signals += 1
        self.stat("decision_makers").signals += 1

    async def company_decision_makers(
        self,
        company: ScoredCompany,
        profiles: Mapping[str, list[dict[str, str]]],
        semaphore: asyncio.Semaphore,
    ) -> None:
        """Profile employees first; Business Search people only when they gave nobody."""
        summary = self.stat("decision_makers")
        summary.companies_searched += 1
        people = list(profiles.get(fold(company.name), []))
        source = "linkedin_companies"
        if not people:
            people, source = await self.search_people(company, semaphore), "business_search_people"
        summary.records += len(people)
        summary.companies_with_dm += int(bool(people))
        if not self.dry_run:
            for person in people:
                await self.send_decision_maker(company, person, source)

    async def phase3(self) -> None:
        source = "decision_makers"
        try:
            companies = await self.top_companies()
            profiles = {} if self.dry_run else await self.profile_decision_makers()
        except (ImportCallError, httpx.HTTPError) as exc:
            self.stat(source).failures.append(f"{type(exc).__name__}: {exc}"[:400])
            return
        print(
            f"  {source}: {len(companies)} scored imported companies, "
            f"{len(profiles)} profiles with decision makers"
        )
        semaphore = asyncio.Semaphore(CONCURRENCY)
        await self.gather(
            source, (self.company_decision_makers(c, profiles, semaphore) for c in companies)
        )

    # ------------------------------------------------------------ accessors

    def _rest(self) -> BrightDataRest:
        if self.rest is None:
            raise ImportCallError("REST client is not configured")
        return self.rest

    def _sdk(self) -> LinkedInSdk:
        if self.sdk is None:
            raise ImportCallError("brightdata-sdk client is not configured")
        return self.sdk

    def _core(self) -> Core:
        if self.core is None:
            raise ImportCallError("core client is not configured")
        return self.core


# ---------------------------------------------------------------- phase 0


async def probe(run: Run, fixtures: Path) -> dict[str, list[dict[str, Any]]]:
    """Make one minimal call per endpoint, write redacted samples, print coverage.

    Each sample keeps the raw provider shape (error records included); person values
    become placeholders and company data stays. Only key paths and counts are printed.
    """
    city = MEDELLIN
    raw: dict[str, list[dict[str, Any]]] = {}
    prepared: dict[str, list[dict[str, Any]]] = {}

    async def attempt(source: str, call: Awaitable[CallResult | None]) -> None:
        try:
            result = await call
        except (BudgetExceededError, ImportCallError, httpx.HTTPError) as exc:
            run.stat(source).failures.append(f"{type(exc).__name__}: {exc}"[:400])
            return
        if result is not None:
            raw[source], prepared[source] = result.raw, result.records

    await attempt("maps", run.maps_call(city, MAPS_KEYWORDS[:1], PROBE_LIMIT))
    await attempt("linkedin_jobs", run.jobs_call(city, JOB_KEYWORDS[:1], PROBE_LIMIT))
    urls: list[str] = []
    for record in raw.get("linkedin_jobs", []):
        url = company_url(record.get("company_url"))
        if url and url not in urls:
            urls.append(url)
    if urls:
        await attempt("linkedin_companies", run.companies_call(urls[:PROBE_LIMIT]))
    elif not run.dry_run:
        run.stat("linkedin_companies").failures.append("no company_url in the jobs sample")
    await attempt(
        "business_search_companies", run.company_search_call(city, "software", PROBE_LIMIT)
    )
    names = [
        str(d["data"]["name"])
        for d in raw.get("business_search_companies", [])
        if isinstance(d.get("data"), dict) and d["data"].get("name")
    ] or [str(r["name"]) for r in raw.get("linkedin_companies", []) if r.get("name")]
    if names:
        body = people_search_body(names[0], PROBE_LIMIT)
        await attempt(
            "business_search_people",
            run.paid_call(
                "business_search_people",
                body,
                PROBE_LIMIT,
                lambda: run._search("people", body),
                billed_records=PROBE_LIMIT,
            ),
        )
    elif not run.dry_run:
        run.stat("business_search_people").failures.append("no company name to search people")

    for source, records in raw.items():
        redacted = [redact_fixture(r, person=source in PERSON_SOURCES) for r in records]
        fixtures.mkdir(parents=True, exist_ok=True)
        target = fixtures / f"{source}.json"
        target.write_text(json.dumps(redacted, indent=2, ensure_ascii=True) + "\n", "utf-8")
        coverage = field_coverage(records)
        print(f"\n{source}: {len(records)} records -> {target}")
        for path in sorted(coverage):
            print(f"  {path}: {coverage[path]}/{len(records)}")
    profiles = prepared.get("linkedin_companies", [])
    if profiles:
        found = sum(len(r.get("decision_makers") or []) for r in profiles)
        print(f"\nlinkedin_companies: {found} decision makers in {len(profiles)} employee lists")
    return raw


# ---------------------------------------------------------------- entry point


async def run_phases(run: Run, phase: str, fixtures: Path) -> None:
    if phase == "0":
        await probe(run, fixtures)
        return
    for number in [1, 2, 3] if phase == "all" else [int(phase)]:
        print(f"phase {number}")
        await getattr(run, f"phase{number}")()


async def execute(args: argparse.Namespace, settings: Settings) -> int:
    phase: str = args.phase
    budget = budget_from_env(os.environ, max_usd=args.max_usd, phase=phase)
    state_dir = Path(
        args.state_dir
        or os.environ.get("BD_IMPORT_STATE_DIR")
        or Path.home() / ".cache" / "invent-bd-import"
    )
    phases = [1, 2, 3] if phase == "all" else ([] if phase == "0" else [int(phase)])
    run = Run(
        budget=budget,
        # The probe always calls; a dry run reads the ledger but never writes it.
        ledger=Ledger(None if phase == "0" else state_dir / "ledger.json"),
        landing=NoLanding(),
        cities=parse_cities(args.cities),
        dry_run=args.dry_run,
        timeout_seconds=args.timeout,
    )
    if args.dry_run:
        run.ledger.path = None
    print(f"bd-import phase {phase}, cities {', '.join(run.cities)}, cap ${budget.max_usd:.2f}")
    if args.dry_run:
        await run_phases(run, phase, args.fixtures)
        print_report(run.stats, budget)
        return 0

    token = settings.bright_data_api_token
    if not token:
        print("BRIGHT_DATA_API_TOKEN is not set (value not shown)", file=sys.stderr)
        return 2
    needed = [s for n in phases for s in PHASE_SOURCES[n] if s in LANDED_DATASETS]
    problems = routing_problems(settings, needed)
    if problems:
        print("The ingest processor would not route these snapshots. Set in .env:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print(
            "then recreate ingest-processor (docs/runbooks/bright-data-import.md).", file=sys.stderr
        )
        return 2

    http = httpx.Client(base_url=settings.bright_data_api_url, timeout=60.0)
    run.rest = BrightDataRest(http, token, settings, timeout_seconds=float(args.timeout))
    repository: IngestRepository | None = None
    core_client: httpx.AsyncClient | None = None
    try:
        if phases:
            repository = IngestRepository(settings.database_url, settings.redis_url)
            run.landing = RepositoryLanding(repository)
        if 3 in phases:
            run.tenant_id = load_ingest_tenant_id()
            # Compose gives the tool its own name for core's URL (CORE_URL is not spread).
            core_url = os.environ.get("BD_IMPORT_CORE_URL") or settings.core_url
            core_client = httpx.AsyncClient(
                base_url=core_url.rstrip("/"), timeout=settings.upstream_timeout_seconds
            )
            run.core = HttpCore(settings, run.tenant_id, core_client)
        async with open_sdk_client(token) as sdk_client:
            run.sdk = LinkedInSdk(sdk_client, run.rest)
            await run_phases(run, phase, args.fixtures)
    finally:
        http.close()
        if core_client is not None:
            await core_client.aclose()
        if repository is not None:
            await repository.close()
    print_report(run.stats, budget)
    return 1 if any(s.failures for s in run.stats.values()) else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import Bright Data data (phases 0-3).")
    parser.add_argument("--phase", required=True, choices=["0", "1", "2", "3", "all"])
    parser.add_argument("--cities", default=None, help="comma list; default: all five")
    parser.add_argument("--dry-run", action="store_true", help="no call, no write")
    parser.add_argument("--max-usd", type=float, default=None, help="overrides BD_IMPORT_MAX_USD")
    parser.add_argument("--timeout", type=int, default=1800, help="seconds per provider call")
    parser.add_argument("--state-dir", default=None, help="ledger folder (BD_IMPORT_STATE_DIR)")
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=ROOT / "tests" / "fixtures" / "brightdata" / "recorded",
        help="phase 0 output folder",
    )
    args = parser.parse_args(argv)
    return asyncio.run(execute(args, get_settings()))


if __name__ == "__main__":
    raise SystemExit(main())
