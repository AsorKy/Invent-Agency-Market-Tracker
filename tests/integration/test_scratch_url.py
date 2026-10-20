"""Contract of the scratch database URL helper (no database needed).

The service tests hand the URL to asyncpg, which accepts only ``postgresql://`` or
``postgres://`` forms. It relates to ``support_sql_pg.scratch_database``.
"""

from support_sql_pg import scratch_url


def test_scratch_url_keeps_the_url_form_and_swaps_only_the_database() -> None:
    url = scratch_url("postgresql://app:s3cr%40t@db.internal:6543/postgres", "imt_scratch")
    assert url == "postgresql://app:s3cr%40t@db.internal:6543/imt_scratch"


def test_scratch_url_accepts_a_url_without_a_database() -> None:
    assert (
        scratch_url("postgresql://localhost", "imt_scratch") == "postgresql://localhost/imt_scratch"
    )
