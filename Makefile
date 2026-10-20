# Reproducible development commands, static guardrails, and Compose
# configuration. Relates to ops/check_*.py, pyproject.toml, and
# docker-compose.yml.
.PHONY: up down datagen openapi static compose-config test test-integration test-sql coverage \
	invent-tracker invent-tracker-down invent-tracker-reset db-ui seed seed-clear doctor verify-live

up:
	docker compose up --build

down:
	docker compose down

datagen:
	python -m datagen build --city all --orgs 200 --days 180 --seed 42

openapi:
	python -m ops.export_openapi

# REQ-RUN-001: one command starts everything; safe to run again. The marker
# .env.seeded (ignored by git through .env.*) records that demo data was loaded.
invent-tracker:
	python ops/bootstrap_env.py
	docker compose up -d --wait postgres redis
	set -a && . ./.env && set +a && python ops/bootstrap_roles.py | docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -q -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'
	docker compose up -d --build --remove-orphans
	python ops/bootstrap_env.py --wait
	test -f .env.seeded || { $(MAKE) seed && touch .env.seeded; }
	@echo "Data UI (SQL editor): make db-ui"

invent-tracker-down:
	docker compose --profile ui down

invent-tracker-reset:
	@printf 'This removes all containers and data volumes. Type yes to continue: '; read answer; \
	if [ "$$answer" = "yes" ]; then docker compose --profile ui down -v && rm -f .env.seeded; else echo "Aborted"; exit 1; fi

# Optional data UI: pgAdmin on loopback with the local database pre-registered. Needs the stack
# from `make invent-tracker`; the login password for the server is POSTGRES_PASSWORD in .env.
db-ui:
	docker compose --profile ui up -d --wait pgadmin
	@echo "pgAdmin: http://localhost:$${PGADMIN_PORT:-5050}  (server 'Invent Market Tracker (local)', user postgres)"

seed:
	docker compose exec -T gateway python -m ops.seed load

seed-clear:
	docker compose exec -T gateway python -m ops.seed clear

doctor:
	python ops/doctor.py

verify-live:
	python ops/verify_live.py --source "$(SOURCE)" $(if $(INPUTS),--inputs "$(INPUTS)")

static:
	python ops/check_migrations.py
	python ops/check_restricted_assets.py
	python ops/check_configuration.py
	python ops/check_language.py
	python ops/check_script_docs.py
	python ops/check_traceability.py
	python ops/check_workflows.py
	python -m compileall -q libs services datagen ops
	ruff check libs services datagen ops
	ruff format --check libs services datagen ops
	mypy libs services datagen ops

compose-config:
	docker compose config >/dev/null


test:
	python -m pytest -q -m "not integration"

test-integration:
	python -m pytest -q -m integration

test-sql:
	TEST_DATABASE_URL="$${TEST_DATABASE_URL:-postgresql://postgres:postgres@localhost:5432/postgres}" python -m pytest -q tests/integration/test_sql_postgres.py

coverage:
	python -m pytest -q -m "not integration" --cov=libs --cov=services --cov-report=term-missing
	python -m coverage report --include='libs/domain/*' --fail-under=90
