# DataCommune — Development commands
# Usage: make <target>

.PHONY: test test-unit test-smoke test-live test-phase67 lint format check dev up down logs validate

# 17-06: API keys travel via the environment, never argv — scripts read
# DC_API_KEY/API_KEY from their inherited environment.
export API_KEY DC_API_KEY BASE_URL

# ── Testing ───────────────────────────────────────────────────────────────

## Run all local tests (unit + smoke, no DB needed)
test:
	cd backend && uv run pytest -v

## Run only unit tests (pure logic, instant)
test-unit:
	cd backend && uv run pytest -v -m unit

## Run only smoke tests (HTTP via ASGI, no DB)
test-smoke:
	cd backend && uv run pytest -v -m smoke

## Run live API tests against an explicit remote target
test-live:
	@if [ -z "$$API_KEY" ]; then echo "ERROR: API_KEY required. Usage: make test-live API_KEY=your_key"; exit 1; fi
	@if [ -z "$$BASE_URL" ]; then echo "ERROR: BASE_URL required; the public API is currently offline."; exit 1; fi
	cd backend && uv run python ../scripts/test_all_endpoints.py \
		--base-url "$$BASE_URL" \
		--skip-proxies

## Run live API tests against local dev
test-local:
	@if [ -z "$$API_KEY" ]; then echo "ERROR: API_KEY required. Usage: make test-local API_KEY=your_key"; exit 1; fi
	cd backend && uv run python ../scripts/test_all_endpoints.py

## Run Phase 67 verification tests (requires API_KEY)
test-phase67:
	@if [ -z "$$API_KEY" ] && [ -z "$$DC_API_KEY" ]; then echo "ERROR: API_KEY required. Usage: make test-phase67 API_KEY=your_key"; exit 1; fi
	cd backend && uv run python ../scripts/test_phase67.py \
		--base-url "$${BASE_URL:-http://localhost:8000}"

# ── Validation ────────────────────────────────────────────────────────────

## Validate DB data against government APIs
validate:
	cd backend && uv run python -m scripts.validate

# ── Code quality ──────────────────────────────────────────────────────────

## Lint + format check (same as CI)
lint:
	cd backend && uv run ruff check app/ tests/
	cd backend && uv run ruff format --check app/ tests/

## Auto-fix lint + format
format:
	cd backend && uv run ruff check --fix app/ tests/
	cd backend && uv run ruff format app/ tests/

## Full pre-push check (lint + tests)
check: lint test

# ── Docker dev ────────────────────────────────────────────────────────────

## Start dev environment (DB + API with hot-reload)
dev:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build

## Start in background
up:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build

## Stop
down:
	docker compose down

## View logs
logs:
	docker compose logs -f api

# ── Help ──────────────────────────────────────────────────────────────────

## Show available commands
help:
	@echo ""
	@echo "  DataCommune — Available commands"
	@echo "  ════════════════════════════════════════════════════"
	@echo ""
	@echo "  Testing:"
	@echo "    make test          Run all local tests (unit + smoke)"
	@echo "    make test-unit     Unit tests only (instant, no deps)"
	@echo "    make test-smoke    Smoke tests only (ASGI, no DB)"
	@echo "    make test-live     Test remote API (needs BASE_URL=... API_KEY=...)"
	@echo "    make test-local    Test local API (needs API_KEY=...)"
	@echo "    make test-phase67  Phase 67 verification (needs API_KEY=...)"
	@echo ""
	@echo "  Code quality:"
	@echo "    make lint          Lint + format check"
	@echo "    make format        Auto-fix lint + format"
	@echo "    make check         Full pre-push (lint + test)"
	@echo ""
	@echo "  Docker:"
	@echo "    make dev           Start dev env (foreground)"
	@echo "    make up            Start dev env (background)"
	@echo "    make down          Stop all containers"
	@echo "    make logs          Follow API logs"
	@echo ""
