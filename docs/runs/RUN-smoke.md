# Smoke run (T-000, closed by T-172)

- git branch: agent/r1-l-platform (from main at 59e68e8)
- commit: made by the coordinator per task; no push from the smoke check
- make static: OK (ruff, ruff format, mypy: no issues in 202 files)
- make test: OK (1607 passed, 2 skipped, 38 deselected, 4 xfailed)
- Python version: 3.12.3 (venv /tmp/venv; default python3 is 3.11 and cannot parse the code)
- pip install '.[dev,streamlit,identity,sqltest]': works
