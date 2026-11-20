# Build context: the repo root (the image bundles the API and the built SPA).
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH" WEB_DIST=/app/web-dist
COPY api/pyproject.toml api/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY api/ ./
COPY --from=web /web/dist /app/web-dist
RUN useradd --system app
USER app
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --no-access-log"]
