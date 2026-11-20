import json
import logging
import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError
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
    ):
        super().__init__(message)
        self.status = status
        self.type = type
        self.message = message
        self.code = code
        self.param = param


def error_response(
    status: int,
    type: str,
    message: str,
    code: str | None = None,
    param: str | None = None,
    headers: dict | None = None,
) -> JSONResponse:
    body = {"type": type, "message": message}
    if code is not None:
        body["code"] = code
    if param is not None:
        body["param"] = param
    return JSONResponse({"error": body}, status_code=status, headers=headers)


def _param_from_loc(loc) -> str | None:
    parts = [str(p) for p in loc]
    if parts and parts[0] in ("body", "query", "path", "header", "cookie"):
        parts = parts[1:]
    if not parts:
        return None
    return parts[0] + "".join(f"[{p}]" for p in parts[1:])


def _validation_response(errors: list[dict]) -> JSONResponse:
    # Built only from `loc`, `type` and `msg`: never `input` or `ctx`, which can hold the
    # submitted value (e.g. a card number).
    first = errors[0]
    param = _param_from_loc(first["loc"])
    if first["type"] == "missing":
        return error_response(
            400,
            "invalid_request_error",
            f"Missing required param: {param}.",
            "parameter_missing",
            param,
        )
    return error_response(
        400,
        "invalid_request_error",
        f"Invalid {param}: {first['msg']}.",
        "parameter_invalid",
        param,
    )


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
        except Exception:
            log.exception("unhandled error request_id=%s", request_id)
            if started:
                raise
            status = 500
            response = error_response(
                500,
                "api_error",
                "An unexpected error occurred.",
                headers={"Request-Id": request_id},
            )
            await response(scope, receive, send)
        finally:
            log.info(
                json.dumps(
                    {
                        "request_id": request_id,
                        "merchant_id": state.get("merchant_id"),
                        "method": scope["method"],
                        "path": scope["path"],
                        "status": status,
                        "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                    }
                )
            )


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return error_response(exc.status, exc.type, exc.message, exc.code, exc.param)

    @app.exception_handler(ValidationError)
    async def _pydantic_error(request: Request, exc: ValidationError):
        return _validation_response(exc.errors())

    @app.exception_handler(RequestValidationError)
    async def _request_validation_error(request: Request, exc: RequestValidationError):
        return _validation_response(list(exc.errors()))

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        if exc.status_code in (404, 405):
            message = f"Unrecognized request URL ({request.method}: {request.url.path})."
        else:
            message = str(exc.detail)
        return error_response(exc.status_code, "invalid_request_error", message)

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
