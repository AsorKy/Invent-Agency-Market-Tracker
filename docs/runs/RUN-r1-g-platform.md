# RUN r1-g-platform

Branch: agent/r1-g-platform

## Tasks done
- T-140: `ops/bootstrap_env.py` now generates BOOTSTRAP_PASSWORD_IDENTITY, BOOTSTRAP_PASSWORD_SOCIO, REDIS_EDGE_PASSWORD and REDIS_DATA_PASSWORD, and rewrites the password in `REDIS_URL` to match the data password. An existing `.env` is never changed; nothing is printed. `.env.example` has comments only.
- T-143: `ops/bootstrap_roles.py` tolerates only SQLSTATE 42501 (insufficient privilege) and 58P01 (undefined file) on extension creation, reports them by name, and raises anything else. Unused `execute()` removed.

## Commands
- `pip install '.[dev,streamlit,identity,sqltest]'` in python3.12 venv: ok
- `pytest -q tests/unit/test_bootstrap_roles_privileges.py tests/unit/test_bootstrap_env.py`: 21 passed
- `python ops/check_configuration.py`: OK (98 variables)
- `make static`: ok; `make test`: 1297 passed, 2 skipped, 4 xfailed
- No SQL changed, so test_sql_consistency was covered by make test only.

## Assumptions
- Compose already defaults every value, so a generated `.env` only replaces the defaults. The Redis ACL passwords come from the same env vars in Compose (docker-compose.yml lines 95-96, 12, 170), so generated values stay consistent.
- Tolerated SQLSTATEs are checked via `exc.sqlstate`, not by exception class.

## Unresolved risks
- No Docker here: the CI e2e stage is the real check of generated Redis/role passwords. A pre-existing `.env` keeps the old defaults, which is intended.
- Redis passwords generated with hex only, so URL-safe.
