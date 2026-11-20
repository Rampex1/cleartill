"""Serve the SPA (web/dist) from the API origin, with the security headers (design §5.10)."""

import os
from pathlib import Path

from fastapi import FastAPI, Request

WEB_DIST = os.environ.get("WEB_DIST") or str(Path(__file__).resolve().parents[2] / "web" / "dist")
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "frame-ancestors 'none'; form-action 'self'"
    ),
    # Keeps the client_secret in the 3DS page URL out of Referer headers.
    "Referrer-Policy": "no-referrer",
}


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response

    # Low priority: API routes always win; the fallback only answers GETs that accept HTML.
    # Without a build (API-only dev, the API CI job) there is no SPA: unknown paths stay JSON
    # 404s instead of failing on the missing directory at request time.
    if Path(WEB_DIST, "index.html").is_file():
        app.frontend("/", directory=WEB_DIST, fallback="index.html")
