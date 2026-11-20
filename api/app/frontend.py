"""Serve the SPA (web/dist) from the API origin, with the security headers (design §5.10)."""

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request

WEB_DIST = os.environ.get("WEB_DIST") or str(Path(__file__).resolve().parents[2] / "web" / "dist")
API_PREFIXES = ("/v1", "/checkout-api", "/dashboard-api")
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
    ),
    # Keeps the client_secret in the 3DS page URL out of Referer headers.
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}
# FastAPI's own docs pages load an inline script and a CDN bundle; they carry no secrets.
DOCS_PATHS = ("/docs", "/redoc")


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        headers = dict(SECURITY_HEADERS)
        if path.startswith(DOCS_PATHS):
            headers.pop("Content-Security-Policy")
        response.headers.update(headers)
        if path.startswith("/assets/"):  # content-hashed by Vite: safe to cache forever
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif response.headers.get("content-type", "").startswith("text/html"):
            # Revalidate index.html so a deploy never strands browsers on deleted assets.
            response.headers["Cache-Control"] = "no-cache"
        return response

    # Unknown API paths stay JSON 404s even for clients whose Accept includes text/html.
    for prefix in API_PREFIXES:

        @app.get(prefix + "/{rest:path}", include_in_schema=False)
        def unknown_api_path(rest: str):
            raise HTTPException(404)

    # Low priority: API routes always win; the fallback only answers GETs that accept HTML.
    # Without a build (API-only dev, the API CI job) there is no SPA: unknown paths stay JSON
    # 404s instead of failing on the missing directory at request time.
    if Path(WEB_DIST, "index.html").is_file():
        app.frontend("/", directory=WEB_DIST, fallback="index.html")
