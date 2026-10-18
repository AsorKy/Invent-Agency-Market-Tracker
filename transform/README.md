# Transform

dbt project that builds the star model (REQ-PUL-003) in the `pulse` schema:

- Dimensions: `dim_company`, `dim_location`, `dim_sector`, `dim_service_line`,
  `dim_source`, `dim_date`, `dim_territory`.
- Facts: `fact_signal`, `fact_company_snapshot`, `fact_lead_outcome`,
  `fact_lead_score`, `fact_territory_indicator`.
- Measures and metrics: `models/semantic.yml` (MetricFlow), defined once.
- Parquet: `python transform/export_parquet.py` writes one file per table to
  `PARQUET_DIR` (default `/data/parquet`) for Streamlit; needs `DATABASE_URL`
  and the `duckdb` package.

Copy `profiles.yml.example` outside the repository (for example
`~/.dbt/profiles.yml`) or point `DBT_PROFILES_DIR` at a CI profile. Do not store
credentials in this directory. Validate without a database with
`dbt parse --project-dir transform --profiles-dir <dir>`.

Warning: the migrations already create `pulse.fact_signal`,
`pulse.fact_company_snapshot` and `pulse.fact_lead_outcome` as tables. Running
`dbt run` against a database with those tables would replace them; resolve the
ownership of those tables before the first run.
