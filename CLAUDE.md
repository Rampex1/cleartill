# Cleartill

Stripe-style payments API (learning clone). Design: `research/design/stripe-clone.md` (local, git-excluded).

- `api/`: FastAPI + SQLAlchemy 2 + Alembic + Postgres (Python 3.13, uv).
- Tests (needs Docker running; testcontainers starts Postgres 16): `cd api && uv run pytest -q`
- Lint: `cd api && uv run ruff check . && uv run ruff format --check .`
- Run locally: `docker compose up -d --build` → `curl localhost:8000/healthz`
- New migration: `cd api && uv run alembic revision -m "..."` (hand-written SQL is fine).
- Tests use a per-test outer transaction with `join_transaction_mode="create_savepoint"`: a `commit()` in code under test only releases a savepoint.
- `app.db` reads `DATABASE_URL` at import; tests get it from `pytest_configure` in `tests/conftest.py`, so top-level `from app.main import app` in test modules is fine.
- API conventions live in `api/app/`: `forms.params` (Stripe form bodies → dict; array fields use `forms.FormList[T]`), `errors.ApiError` (Stripe error envelope), `auth.require_secret_key`/`require_publishable_key`, `ids.new_id(prefix)`, `clock.now()` (monkeypatch in tests), `urls.validate_redirect_url`.
- Never put submitted values into error messages or logs (validation messages use our own wording per Pydantic error type; the engine has `hide_parameters=True`).
- `app/tenancy.py`: load every object with `get_owned(db, Model, id, merchant_id, param=…)` (404 for other tenants; `for_update=True` for writes) and list with `paginate`. Validate client params with `forms.validate(Model, params)` (never `Model.model_validate` on client input).
- `app/executor.py`: every `/v1` write route returns `execute(db, request, auth.merchant.id, params, handler)`; the handler returns a dict or `Result(status, body)` and raises `ApiError` for 4xx. Handlers never call `db.commit()`. FastAPI dependencies (auth etc.) must only read: their writes would sit outside the executor's savepoint. `Idempotency-Key` (POST only) is handled there.
- Dashboard auth: session cookie `cleartill_session` (HttpOnly, Secure, SameSite=Lax, 7 days) also authenticates `/v1`; cookie-authenticated writes need `X-Cleartill-Dashboard: 1`. `/dashboard-api` uses `require_session` (cookie only). Tests with cookies need `TestClient(app, base_url="https://testserver")`. Login/signup are rate-limited per `ratelimit.client_ip` (`TRUSTED_PROXY_HOPS`, default 0 = ignore X-Forwarded-For).
- Ledger (`app/ledger.py`): `post()` is the only journal writer (one multi-row INSERT). Postgres triggers reject unbalanced/single-leg/multi-currency/appended transactions and any UPDATE/DELETE of journal rows, so committed-data tests must not create journal entries they need to clean up (rely on the savepoint `db` fixture). Money tests should end with `assert ledger_check(db, own_txn=False) == []`.
