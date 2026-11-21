#!/bin/sh
# Container entrypoint (Render and the image default): migrate, then serve. A script rather
# than an inline `sh -c "..."`, because hosts that wrap the start command mangle the quotes.
set -e
alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --no-access-log
