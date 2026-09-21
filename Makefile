.PHONY: setup import seed run test clean

setup:
	python3 -m venv .venv
	.venv/bin/pip install -q -r requirements.txt

import: setup
	rm -f berths.db
	.venv/bin/python -m app.import_schedule data/years/dock_schedule_*.csv

seed: setup
	rm -f berths.db
	.venv/bin/python -m app.seed

run:
	.venv/bin/uvicorn app.main:app --reload --port 8420

test: setup
	.venv/bin/pytest -q

clean:
	rm -rf .venv berths.db .pytest_cache __pycache__ app/__pycache__ tests/__pycache__
