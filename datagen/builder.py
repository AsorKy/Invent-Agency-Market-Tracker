"""Generate deterministic synthetic companies, signals, and leads.

``build_dataset`` uses a seed, UUIDv5, and stable reference date so two runs
produce the same dataset; ``main`` serializes the collections to JSON. It
relates to ``datagen/__main__.py``, ``ops/seed/README.md``, and the structures
in ``migrations``. It is used in CI and development to validate contracts
before real Colombian data sources are connected.
"""

import argparse
import json
import random
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

CITIES = {
    "medellin": ("Medellin", "Antioquia"),
    "bogota": ("Bogota", "Cundinamarca"),
    "cali": ("Cali", "Valle del Cauca"),
}
SIGNAL_TYPES = ("job_opening", "maps_opening", "review", "digital_gap")
SERVICE_LINES = (
    "saas",
    "aaas",
    "software_consulting",
    "data_analytics",
    "digital_marketing",
    "event_catering",
)
SECTORS = ("G", "I", "J", "H", "Q", "L")


def _company_id(city: str, index: int) -> str:
    return str(uuid5(NAMESPACE_URL, f"invent-company:{city}:{index}"))


def build_dataset(
    *, city: str, orgs: int, days: int, seed: int, as_of: date | None = None
) -> dict[str, list[dict[str, object]]]:
    if orgs < 1:
        raise ValueError("orgs must be greater than zero")
    if days < 1:
        raise ValueError("days must be greater than zero")
    rng = random.Random(seed)
    selected_cities = list(CITIES) if city == "all" else [city]
    if any(value not in CITIES for value in selected_cities):
        raise ValueError(f"unknown city: {city}")

    companies: list[dict[str, object]] = []
    signals: list[dict[str, object]] = []
    leads: list[dict[str, object]] = []
    anchor = as_of or date(2026, 1, 1)
    start = datetime.combine(anchor - timedelta(days=days), datetime.min.time(), tzinfo=UTC)

    for index in range(orgs):
        city_key = selected_cities[index % len(selected_cities)]
        city_name, department = CITIES[city_key]
        company_id = _company_id(city_key, index)
        companies.append(
            {
                "id": company_id,
                "slug": f"synthetic-company-{index:04d}",
                "legal_name": f"Synthetic Company {index:04d} SAS",
                "nit": f"900{index:06d}-1",
                "city": city_name,
                "department": department,
                "sector_code": rng.choice(SECTORS),
                "employee_count": rng.randint(20, 500),
                "website": None
                if rng.random() < 0.28
                else f"https://synthetic-company-{index:04d}.example.co",
            }
        )

    for signal_index in range(max(orgs * 7, 1)):
        company = companies[signal_index % len(companies)]
        observed = start + timedelta(days=rng.randrange(days), hours=rng.randrange(24))
        signals.append(
            {
                "id": str(
                    uuid5(NAMESPACE_URL, f"invent-signal:{seed}:{company['id']}:{signal_index}")
                ),
                "company_id": company["id"],
                "source": rng.choice(("bright_data_maps", "computrabajo", "synthetic")),
                "signal_type": rng.choice(SIGNAL_TYPES),
                "observed_at": observed.isoformat(),
                "strength": round(rng.uniform(0.25, 1), 4),
            }
        )

    for lead_index, company in enumerate(companies[: min(50, len(companies))]):
        leads.append(
            {
                "id": str(uuid5(NAMESPACE_URL, f"invent-lead:{seed}:{company['id']}:{lead_index}")),
                "company_id": company["id"],
                "service_line": rng.choice(SERVICE_LINES),
                "stage": rng.choice(("new", "contacted", "meeting", "proposal")),
                "outcome": rng.choice(("no_response", "responded", "meeting", "pending")),
            }
        )

    return {"companies": companies, "signals": signals, "leads": leads}


def build_snapshots(
    dataset: dict[str, list[dict[str, object]]], *, seed: int
) -> dict[str, list[dict[str, object]]]:
    """Return provider-shaped records for the ``maps`` and ``computrabajo`` sources.

    Field names are plausible Bright Data dataset names (no versioned mapping file
    exists yet). Only company-level data is emitted; no personal data.
    """
    rng = random.Random(f"invent-snapshots:{seed}")
    companies = {str(c["id"]): c for c in dataset["companies"]}
    maps: list[dict[str, object]] = []
    for company in dataset["companies"]:
        maps.append(
            {
                "place_id": f"ChIJ{str(company['id']).replace('-', '')[:24]}",
                "name": company["legal_name"],
                "category": rng.choice(("Software company", "Restaurant", "Marketing agency")),
                "address": f"Calle {rng.randint(1, 99)} # {rng.randint(1, 99)}",
                "city": company["city"],
                "country": "CO",
                "website": company["website"],
                "rating": round(rng.uniform(3.0, 5.0), 1),
                "reviews_count": rng.randint(0, 800),
                "url": f"https://maps.example.com/place/{company['slug']}",
            }
        )
    jobs: list[dict[str, object]] = []
    for signal in dataset["signals"]:
        if signal["signal_type"] != "job_opening":
            continue
        company = companies[str(signal["company_id"])]
        jobs.append(
            {
                "job_id": f"ct-{str(signal['id'])[:12]}",
                "job_title": rng.choice(("Data Analyst", "Developer", "Marketing Lead", "Chef")),
                "company_name": company["legal_name"],
                "location": f"{company['city']}, Colombia",
                "date_posted": signal["observed_at"],
                "salary_range": None,
                "url": f"https://computrabajo.example.com/oferta/{str(signal['id'])[:12]}",
            }
        )
    return {"maps": maps, "computrabajo": jobs}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build deterministic synthetic Invent data")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--city", default="all", choices=["all", *CITIES])
    build.add_argument("--orgs", type=int, default=200)
    build.add_argument("--days", type=int, default=180)
    build.add_argument("--seed", type=int, default=42)
    build.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 1, 1))
    build.add_argument("--output", type=Path, default=Path("data/generated"))
    build.add_argument(
        "--as-snapshots",
        action="store_true",
        help="also emit provider-shaped maps and computrabajo snapshot files",
    )
    args = parser.parse_args()

    try:
        dataset = build_dataset(
            city=args.city, orgs=args.orgs, days=args.days, seed=args.seed, as_of=args.as_of
        )
    except ValueError as exc:
        parser.error(str(exc))
    args.output.mkdir(parents=True, exist_ok=True)
    for name, rows in dataset.items():
        (args.output / f"{name}.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if args.as_snapshots:
        for name, rows in build_snapshots(dataset, seed=args.seed).items():
            (args.output / f"snapshot_{name}.json").write_text(
                json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    print(f"Generated {sum(len(rows) for rows in dataset.values())} records in {args.output}")
    return 0
