"""Command line entry point: ``python -m ops.seed load|clear`` (REQ-RUN-003).

``load`` feeds the datagen demo snapshots through the ingest flow into core's internal API, then
scores and briefs each company through the ML and Brief internal APIs (``ops/seed/enrich.py``);
``clear`` removes the demo companies and signals through core's API. Both read ``CORE_URL`` and
``INTERNAL_SERVICE_TOKEN`` from the settings and accept the datagen parameters. An injected core
without an injected scorer (tests) skips the enrichment, so offline runs never open a network
connection. It relates to ``ops/seed/loader.py`` and the ``seed`` and ``seed-clear`` make targets.
"""

import argparse
import asyncio
from collections.abc import Sequence
from datetime import date
from typing import Any

from libs.common.settings import get_settings
from ops.seed import enrich, loader


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ops.seed", description="Load or clear demo data.")
    parser.add_argument("command", choices=["load", "clear"])
    parser.add_argument("--orgs", type=int, default=loader.DEFAULT_ORGS)
    parser.add_argument("--days", type=int, default=loader.DEFAULT_DAYS)
    parser.add_argument("--seed", type=int, default=loader.DEFAULT_SEED)
    parser.add_argument("--as-of", type=date.fromisoformat, default=loader.DEFAULT_AS_OF)
    parser.add_argument(
        "--no-enrich", action="store_true", help="load only; skip scores and briefs"
    )
    return parser


async def _enrich(
    profiles: dict[str, enrich.CompanyProfile],
    settings: Any,
    scorer: enrich.ScoreBriefPort | None,
) -> int:
    owned = scorer is None
    port = scorer or enrich.HttpScoreBrief(
        settings.ml_url, settings.brief_url, settings.internal_service_token, loader.DEMO_TENANT_ID
    )
    try:
        outcome = await enrich.enrich(port, profiles, sleep=asyncio.sleep)
    finally:
        if owned and isinstance(port, enrich.HttpScoreBrief):
            await port.aclose()
    print(f"seed enrich: scored={outcome.scored} briefed={outcome.briefed} failed={outcome.failed}")
    for reason, count in sorted(outcome.reasons.items(), key=lambda item: -item[1])[:5]:
        print(f"seed enrich failure: {reason} x{count}")
    return 0 if outcome.failed == 0 else 1


async def run(
    argv: Sequence[str] | None = None,
    core: loader.SeedCore | None = None,
    scorer: enrich.ScoreBriefPort | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    owned = core is None
    client = core or loader.http_core(settings.core_url, settings.internal_service_token)
    params = {"orgs": args.orgs, "days": args.days, "seed": args.seed, "as_of": args.as_of}
    try:
        if args.command == "load":
            result = await loader.load(client, **params)
            print(
                f"seed load: delivered={result.delivered} skipped={result.skipped} "
                f"failed={result.failed}"
            )
            for reason, count in sorted(result.reasons.items(), key=lambda item: -item[1])[:5]:
                print(f"seed failure: {reason} x{count}")
            if result.failed:
                return 1
            if args.no_enrich or (not owned and scorer is None):
                return 0
            return await _enrich(result.profiles, settings, scorer)
        cleared = await loader.clear(client, **params)
        print(f"seed clear: removed={cleared.removed}")
        return 0
    finally:
        if owned and isinstance(client, loader.HttpSeedCore):
            await client.aclose()


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
