# Reproducible development commands, static guardrails, and Compose
# configuration. Relates to ops/check_*.py, pyproject.toml, and
# docker-compose.yml.
.PHONY: up down datagen static compose-config test test-integration coverage

up:
	docker compose up --build

down:
	docker compose down

datagen:
	python -m datagen build --city all --orgs 200 --days 180 --seed 42

static:
	python ops/check_migrations.py
	python ops/check_restricted_assets.py
	python ops/check_configuration.py
	python ops/check_language.py
	python ops/check_script_docs.py
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

coverage:
	python -m pytest -q -m "not integration" --cov=libs --cov=services --cov-report=term-missing
