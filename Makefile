.PHONY: install up down migrate lint test

install:
	pip install -e ".[dev]"

up:
	docker compose up -d

down:
	docker compose down

migrate:
	alembic upgrade head

# Los mismos objetivos que CI, en el mismo orden: si pasa aqui, pasa alli.
lint:
	ruff check src tests scripts
	ruff format --check src tests scripts
	mypy

test:
	pytest -q
