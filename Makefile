.PHONY: setup up down migrate test lint typecheck eval bench demo api tunnel

setup:  ## Install dependencies
	uv sync

up:  ## Start Postgres (port 5441)
	docker compose up -d postgres

down:  ## Stop the services (data volume is kept)
	docker compose down

migrate:  ## Apply database migrations (as the migrator role)
	uv run python -m app.db.migrate

test:  ## Unit and integration tests (no live API calls, no cost)
	uv run pytest -q

lint:  ## Style checks
	uv run ruff check .
	uv run ruff format --check .

typecheck:  ## Basic type checking
	uv run mypy

api:  ## The tool server on :8011 (added in Phase 2)
	uv run uvicorn app.api.main:create_app --factory --port 8011

tunnel:  ## Free cloudflared quick tunnel to :8011; copy the printed URL into PUBLIC_BASE_URL and the Vapi config
	cloudflared tunnel --url http://127.0.0.1:8011

eval:  ## Run the 15 scenarios x 3 runs (Phase 1: the reference agent proves harness + grader; Phase 3 adds the live agent)
	uv run python -m evals.run --runs 3

# bench, demo and sync-assistant arrive with Phases 3 and 5.
bench demo:
	@echo "not built yet (see PLAN.md phases)"
