# Cleartill

Stripe-style payments API (learning clone). Design: `research/design/stripe-clone.md` (local, git-excluded).

- `api/`: FastAPI + SQLAlchemy 2 + Alembic + Postgres (Python 3.13, uv).
- Tests (needs Docker running; testcontainers starts Postgres 16): `cd api && uv run pytest -q`
- Lint: `cd api && uv run ruff check . && uv run ruff format --check .`
- Run locally: `docker compose up -d --build` → `curl localhost:8000/healthz`
- New migration: `cd api && uv run alembic revision -m "..."` (hand-written SQL is fine).
- Tests use a per-test outer transaction with `join_transaction_mode="create_savepoint"`: a `commit()` in code under test only releases a savepoint.
