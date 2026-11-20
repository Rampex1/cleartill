"""Exception classes, one per row of the API's error table."""


class CleartillError(Exception):
    def __init__(
        self,
        message: str,
        *,
        http_status: int | None = None,
        code: str | None = None,
        decline_code: str | None = None,
        param: str | None = None,
        request_id: str | None = None,
        json_body: dict | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.http_status = http_status
        self.code = code
        self.decline_code = decline_code
        self.param = param
        self.request_id = request_id
        self.json_body = json_body

    def __str__(self) -> str:
        prefix = f"Request {self.request_id}: " if self.request_id else ""
        return f"{prefix}{self.message}"


class InvalidRequestError(CleartillError):
    pass


class IdempotencyError(CleartillError):
    pass


class AuthenticationError(CleartillError):
    pass


class CardError(CleartillError):
    pass


class PermissionError(CleartillError):  # noqa: A001 (mirrors the API's error class name)
    pass


class RateLimitError(CleartillError):
    pass


class APIError(CleartillError):
    pass


class APIConnectionError(CleartillError):
    pass


class SignatureVerificationError(CleartillError):
    pass


def error_for(status: int, body: dict, request_id: str | None) -> CleartillError:
    err = body.get("error") if isinstance(body, dict) else None
    err = err if isinstance(err, dict) else {}
    kind = err.get("type")
    if kind == "idempotency_error":
        cls = IdempotencyError
    elif status == 401:
        cls = AuthenticationError
    elif status == 402:
        cls = CardError
    elif status == 403:
        cls = PermissionError
    elif status == 429:
        cls = RateLimitError
    elif status in (400, 404):
        cls = InvalidRequestError
    else:
        cls = APIError
    return cls(
        err.get("message") or f"HTTP {status}",
        http_status=status,
        code=err.get("code"),
        decline_code=err.get("decline_code"),
        param=err.get("param"),
        request_id=request_id,
        json_body=body if isinstance(body, dict) else None,
    )
