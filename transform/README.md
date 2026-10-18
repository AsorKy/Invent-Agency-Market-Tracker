# Transform

dbt project that builds the star model (REQ-PUL-003) in the `pulse` schema:

- Dimensions: `mart_dim_company`, `mart_dim_location`, `mart_dim_sector`, `mart_dim_service_line`,
  `mart_dim_source`, `mart_dim_date`, `mart_dim_territory`.
- Facts: `mart_fact_signal`, `mart_fact_company_snapshot`, `mart_fact_lead_outcome`,
  `mart_fact_lead_score`, `mart_fact_territory_indicator`.
- Measures and metrics: `models/semantic.yml` (MetricFlow), defined once.
- Parquet: `python transform/export_parquet.py` writes one file per table to
  `PARQUET_DIR` (default `/data/parquet`) for Streamlit; needs `DATABASE_URL`
  and the `duckdb` package.

Copy `profiles.yml.example` outside the repository (for example
`~/.dbt/profiles.yml`) or point `DBT_PROFILES_DIR` at a CI profile. Do not store
credentials in this directory. Validate without a database with
`dbt parse --project-dir transform --profiles-dir <dir>`.

Naming (decision D-19): `pulse.fact_*` tables are projections created by
migrations and written by the pulse consumer; `*_rollup` are rollups; dbt staging
views are `stg_<source>__<entity>`; dbt marts are `mart_dim_<name>` and
`mart_fact_<name>`, so dbt never collides with a migration-created table.
