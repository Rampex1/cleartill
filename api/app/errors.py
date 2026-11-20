import json
import logging
import time
import traceback

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.ids import new_id

log = logging.getLogger("cleartill")

# lock_not_available (lock_timeout) and deadlock_detected
LOCK_SQLSTATES = {"55P03", "40P01"}


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        type: str,
        message: str,
        code: str | None = None,
        param: str | None = None,
        headers: dict | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.type = type
        self.message = message
        self.code = code
        self.param = param
        self.headers = headers


def error_response(
    status: int,
    type: str,
    message: str,
    code: str | None = None,
    param: str | None = None,
    headers: dict | None = None,
    request_id: str | None = None,
) -> JSONResponse:
    body = {"type": type, "message": message}
    if code is not None:
        body["code"] = code
    if param is not None:
        body["param"] = param
    if request_id is not None:
        body["request_id"] = request_id
    return JSONResponse({"error": body}, status_code=status, headers=headers)


def _param_from_loc(loc) -> str | None:
    # Pydantic marks errors on a dict *key* with a trailing "[key]" segment; drop it.
    parts = [str(p) for p in loc if p != "[key]"]
    if parts and parts[0] in ("body", "query", "path", "header", "cookie"):
        parts = parts[1:]
    if not parts:
        return None
    return parts[0] + "".join(f"[{p}]" for p in parts[1:])


# Our own wording per Pydantic error type. Pydantic's `msg`, `input` and `ctx` are never used:
# some messages (discriminated unions, custom validators) contain the submitted value.
_PROBLEMS = {
    "int_parsing": "must be an integer",
    "int_type": "must be an integer",
    "int_from_float": "must be an integer",
    "bool_parsing": "must be true or false",
    "bool_type": "must be true or false",
    "string_type": "must be a string",
    "string_too_short": "is too short",
    "string_too_long": "is too long",
    "greater_than": "is too small",
    "greater_than_equal": "is too small",
    "less_than": "is too large",
    "less_than_equal": "is too large",
    "literal_error": "is not one of the allowed values",
    "enum": "is not one of the allowed values",
    "union_tag_invalid": "is not one of the allowed values",
    "dict_type": "must be a hash (use name[key]=value)",
    "list_type": "must be an array (use name[]=value)",
    "too_long": "has too many items",
    "list_indexes": "must be an array with indexes 0, 1, 2, …",
}


def _validation_response(errors: list[dict]) -> JSONResponse:
    first = errors[0]
    kind = first["type"]
    param = _param_from_loc(first["loc"])
    subject = f"param {param}" if param else "request"
    if kind == "missing":
        return error_response(
            400,
            "invalid_request_error",
            f"Missing required {subject}.",
            "parameter_missing",
            param,
        )
    if kind == "extra_forbidden":
        return error_response(
            400,
            "invalid_request_error",
            f"Received unknown parameter: {param}.",
            "parameter_unknown",
            param,
        )
    problem = _PROBLEMS.get(kind, "is invalid")
    return error_response(
        400,
        "invalid_request_error",
        f"Invalid {subject}: it {problem}.",
        "parameter_invalid",
        param,
    )


def _log_unhandled(request_id: str, exc: BaseException) -> None:
    if isinstance(exc, DBAPIError):
        # The driver's message can carry the server's DETAIL line with row values; log only
        # the type, SQLSTATE and stack frames.
        frames = "".join(traceback.format_tb(exc.__traceback__))
        log.error(
            "unhandled %s sqlstate=%s request_id=%s\n%s",
            type(exc.orig).__name__,
            getattr(exc.orig, "sqlstate", None),
            request_id,
            frames,
        )
    else:
        log.exception("unhandled error request_id=%s", request_id)


class RequestIdMiddleware:
    """Adds Request-Id to every response, turns unhandled errors into a 500 envelope,
    and writes one JSON log line per request (path only, never the query string)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = new_id("req_")
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        start = time.perf_counter()
        status = 500
        started = False

        async def send_with_id(message):
            nonlocal status, started
            if message["type"] == "http.response.start":
                started = True
                status = message["status"]
                message["headers"] = [
                    *message.get("headers", []),
                    (b"request-id", request_id.encode()),
                ]
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception as exc:
            _log_unhandled(request_id, exc)
            status = 500
            if started:
                raise
            response = error_response(
                500,
                "api_error",
                "An unexpected error occurred.",
                headers={"Request-Id": request_id},
                request_id=request_id,
            )
            await response(scope, receive, send)
        finally:
            # Log the route template (/v1/x/{id}), not the concrete path: ids such as cs_…
            # are capabilities. Unmatched paths are truncated.
            route = scope.get("route")
            path = getattr(route, "path", None) or scope["path"][:200]
            log.info(
                json.dumps(
                    {
                        "request_id": request_id,
                        "merchant_id": state.get("merchant_id"),
                        "method": scope["method"],
                        "path": path,
                        "status": status,
                        "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                    }
                )
            )


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return error_response(
            exc.status, exc.type, exc.message, exc.code, exc.param, headers=exc.headers
        )

    @app.exception_handler(ValidationError)
    async def _pydantic_error(request: Request, exc: ValidationError):
        return _validation_response(exc.errors())

    @app.exception_handler(RequestValidationError)
    async def _request_validation_error(request: Request, exc: RequestValidationError):
        return _validation_response(list(exc.errors()))

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        if exc.status_code in (404, 405):
            message = f"Unrecognized request URL ({request.method})."
        else:
            message = str(exc.detail)
        return error_response(
            exc.status_code, "invalid_request_error", message, headers=exc.headers
        )

    @app.exception_handler(OperationalError)
    async def _operational_error(request: Request, exc: OperationalError):
        if getattr(exc.orig, "sqlstate", None) in LOCK_SQLSTATES:
            return error_response(
                409,
                "api_error",
                "The request conflicted with another request; retry it.",
                "lock_timeout",
            )
        raise exc

    app.add_middleware(RequestIdMiddleware)
