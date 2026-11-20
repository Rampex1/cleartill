import os
import re
from urllib.parse import urlsplit

from app.errors import ApiError

MAX_URL_LENGTH = 2048
# Whitespace/control chars, plus characters browsers and Python disagree on (\\) or that are
# dangerous if the URL is ever echoed into HTML ("'<>).
_FORBIDDEN = re.compile(r"[\s\x00-\x1f\x7f\\\"'<>]")


def validate_redirect_url(url: str, param: str) -> str:
    """Only absolute https URLs (http too when ALLOW_INSECURE_URLS=1, local dev).

    Blocks javascript:, data: and friends, which would otherwise run on our origin when the
    checkout page redirects (design §5.10)."""
    schemes = {"https"} | ({"http"} if os.environ.get("ALLOW_INSECURE_URLS") == "1" else set())
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        parts, host = None, None
    if (
        len(url) > MAX_URL_LENGTH
        or _FORBIDDEN.search(url)
        or parts is None
        or parts.scheme not in schemes
        or not host
        or parts.username is not None
    ):
        raise ApiError(
            400,
            "invalid_request_error",
            f"Invalid URL for {param}: it must be an absolute https URL.",
            code="url_invalid",
            param=param,
        )
    return url
