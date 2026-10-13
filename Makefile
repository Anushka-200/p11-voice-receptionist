.PHONY: setup up down migrate test lint typecheck eval bench demo api tunnel sync-assistant sync-assistant-apply eval-live check-assistant dashboard worker

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

eval:  ## 15 scenarios x 3 runs with the reference agent (free): proves the harness and grader
	uv run python -m evals.run --runs 3

eval-live:  ## Same scenarios with the real prompt + tools + a simulated caller (about $0.30; needs ANTHROPIC_API_KEY)
	uv run python -m evals.run --agent live --runs 3 --workers 4

sync-assistant:  ## Dry run: show what would be pushed to Vapi (nothing is sent)
	uv run python -m scripts.sync_assistant

dashboard:  ## Call analytics dashboard on http://localhost:8511 (needs the optional dashboard group)
	uv run --group dashboard streamlit run dashboard/app.py --server.port 8511

worker:  ## Analyse any pending calls and deliver CRM notes (one pass)
	uv run python -m app.postcall

check-assistant:  ## Read-only: is what Vapi holds identical to the local prompt and config?
	uv run python -m scripts.check_assistant

sync-assistant-apply:  ## Really push the tools and assistant to your Vapi account
	uv run python -m scripts.sync_assistant --apply

# bench and demo arrive with Phase 5.
bench:  ## Latency, cost per minute and quality tables from the stored calls
	uv run python -m app.bench

demo:
	@echo "not built yet (see PLAN.md phases)"
