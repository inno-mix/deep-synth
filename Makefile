.PHONY: setup dev backend frontend phoenix test lint research types

TOPIC ?= Compare PostgreSQL pgvector vs Qdrant performance

setup:            ## install deps and pull the local model
	cd backend && uv sync
	cd frontend && pnpm install
	ollama pull qwen2.5:7b
	@test -f .env || cp .env.example .env

phoenix:          ## local tracing UI at http://localhost:6006
	docker compose up -d phoenix

backend:          ## FastAPI on :8000
	cd backend && uv run uvicorn app.main:app --reload --port 8000

frontend:         ## Next.js on :3000
	cd frontend && pnpm dev

dev:              ## everything (Ctrl-C stops both servers)
	$(MAKE) phoenix
	$(MAKE) -j2 backend frontend

research:         ## run the graph from the terminal: make research TOPIC="..."
	cd backend && uv run python -m app.cli "$(TOPIC)"

test:
	cd backend && uv run pytest -q

lint:
	cd backend && uv run ruff check app tests
	cd frontend && pnpm lint && pnpm typecheck

types:            ## regenerate frontend types from the FastAPI schema
	cd frontend && pnpm gen:types
