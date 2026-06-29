.PHONY: start listener test lint

start:
	uv run python scripts/start_app.py

listener:
	uv run python scripts/start_with_tunnel.py

test:
	uv run python -m unittest discover -s tests

lint:
	uv run --with ruff ruff check .
