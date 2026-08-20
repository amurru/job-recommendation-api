.PHONY: install lint format typecheck test run

install:
	uv sync --group dev

lint:
	uv run ruff check .

format:
	uv run ruff format .

typecheck:
	uv run mypy src tests

test:
	uv run pytest

run:
	uv run job-recommendation-api