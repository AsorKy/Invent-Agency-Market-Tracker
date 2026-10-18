"""Export the dbt star model (dimensions and facts in `pulse`) to Parquet.

Relates to models/marts and REQ-PUL-003. Streamlit reads the files from the
volume shared with the transform job (default /data/parquet, override with
PARQUET_DIR). The script reads from Postgres with DuckDB and writes one file per
table, replacing it atomically. Connection settings come from DATABASE_URL; no
credentials are stored here.

Usage: python transform/export_parquet.py [--out-dir DIR]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

TABLES = (
    "dim_company",
    "dim_location",
    "dim_sector",
    "dim_service_line",
    "dim_source",
    "dim_date",
    "dim_territory",
    "fact_signal",
    "fact_company_snapshot",
    "fact_lead_outcome",
    "fact_lead_score",
    "fact_territory_indicator",
)
SCHEMA = "pulse"


def export(database_url: str, out_dir: Path) -> list[Path]:
    """Write every table in TABLES to out_dir and return the written paths."""
    import duckdb  # imported lazily so the module loads without the dependency

    out_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("INSTALL postgres; LOAD postgres;")
    con.execute("ATTACH ? AS pg (TYPE postgres, READ_ONLY)", [database_url])
    written: list[Path] = []
    for table in TABLES:
        final = out_dir / f"{table}.parquet"
        tmp = out_dir / f"{table}.parquet.tmp"
        con.execute(
            f"COPY (SELECT * FROM pg.{SCHEMA}.{table}) TO '{tmp}' (FORMAT parquet)"
        )
        tmp.replace(final)
        written.append(final)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir", default=os.environ.get("PARQUET_DIR", "/data/parquet")
    )
    args = parser.parse_args(argv)
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is required", file=sys.stderr)
        return 2
    for path in export(database_url, Path(args.out_dir)):
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
