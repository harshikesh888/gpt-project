.PHONY: install run test lint format

install:
	python3 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -r requirements-dev.txt

run:
	.venv/bin/uvicorn main:app --reload --port 8000 --no-server-header

test:
	.venv/bin/pytest

lint:
	.venv/bin/ruff check .

format:
	.venv/bin/ruff check . --fix
