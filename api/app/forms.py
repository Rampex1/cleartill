"""Stripe-style form parameters: `a[b]=1`, `a[]=x`, `a[0][n]=1` → nested dicts and lists.

`parse_params` is schema-agnostic: `a[0][n]=1` stays a dict keyed "0", because a hash such as
`metadata[12345]=x` legitimately has digit keys. Fields that really are arrays declare
`FormList[T]`, which turns a digit-keyed dict into a list.
"""

import re
from typing import Annotated
from urllib.parse import parse_qsl

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
)
from pydantic_core import PydanticCustomError

from app.errors import ApiError

MAX_PAIRS = 1000
MAX_DEPTH = 5
MAX_BODY_BYTES = 64 * 1024

_KEY = re.compile(r"^([^\[\]]+)((?:\[[^\[\]]*\])*)$")
_SEGMENT = re.compile(r"\[([^\[\]]*)\]")


MAX_PARAM_ECHO = 100


def _invalid(param: str, message: str) -> ApiError:
    # Messages never contain the key or value; `param` is truncated.
    return ApiError(
        400,
        "invalid_request_error",
        message,
        code="parameter_invalid",
        param=param[:MAX_PARAM_ECHO],
    )


def parse_params(pairs: list[tuple[str, str]]) -> dict:
    if len(pairs) > MAX_PAIRS:
        raise _invalid(pairs[MAX_PAIRS][0], f"Too many parameters (max {MAX_PAIRS}).")
    root: dict = {}
    for key, value in pairs:
        if "\x00" in key or "\x00" in value:
            raise _invalid(key, "Parameters may not contain NUL characters.")
        match = _KEY.match(key)
        if not match:
            raise _invalid(key, "Malformed parameter name.")
        path = [match.group(1), *_SEGMENT.findall(match.group(2))]
        if len(path) - 1 > MAX_DEPTH:
            raise _invalid(key, f"Parameters may be nested at most {MAX_DEPTH} levels deep.")
        _insert(root, path, value, key)
    return root


def _insert(root: dict, path: list[str], value: str, key: str) -> None:
    node = root
    for i, segment in enumerate(path):
        last = i == len(path) - 1
        if segment == "":
            if not last:
                raise _invalid(key, "[] is only allowed at the end of a parameter name.")
            node.append(value)
            return
        if last:
            if segment in node:
                raise _invalid(key, "Parameter was given more than once.")
            node[segment] = value
            return
        container = list if path[i + 1] == "" else dict
        child = node.get(segment)
        if child is None:
            child = node[segment] = container()
        elif not isinstance(child, container):
            raise _invalid(key, "Parameter mixes incompatible forms (value, hash or array).")
        node = child


def _indexed_to_list(value):
    """`{"0": a, "1": b}` (from `x[0]=a&x[1]=b`) → `[a, b]`; indexes must be 0..n-1."""
    if not isinstance(value, dict):
        return value
    if set(value) != {str(i) for i in range(len(value))}:
        raise PydanticCustomError("list_indexes", "array indexes must be 0..n-1")
    return [value[str(i)] for i in range(len(value))]


type FormList[T] = Annotated[list[T], BeforeValidator(_indexed_to_list)]


async def params(request: Request) -> dict:
    if request.method in ("GET", "HEAD", "DELETE"):
        return parse_params(parse_qsl(request.url.query, keep_blank_values=True))
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY_BYTES:
            raise ApiError(400, "invalid_request_error", "Request body is too large (max 64 KB).")
    if body:
        content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type != "application/x-www-form-urlencoded":
            raise ApiError(
                400,
                "invalid_request_error",
                "Invalid request: unsupported Content-Type. Use application/x-www-form-urlencoded.",
            )
    try:
        text = body.decode()
    except UnicodeDecodeError:
        raise ApiError(400, "invalid_request_error", "Request body is not valid UTF-8.") from None
    return parse_params(parse_qsl(text, keep_blank_values=True))


def validate[M: BaseModel](model: type[M], params: dict) -> M:
    """Validate client params. Failures become the standard 400; a Pydantic error raised
    anywhere else is a server bug and surfaces as a 500."""
    try:
        return model.model_validate(params)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from None


# Stripe limits: 50 keys, keys up to 40 chars (no brackets), values up to 500 chars.
MetadataKey = Annotated[str, StringConstraints(min_length=1, max_length=40, pattern=r"^[^\[\]]+$")]
MetadataValue = Annotated[str, StringConstraints(max_length=500)]
Metadata = Annotated[dict[MetadataKey, MetadataValue], Field(max_length=50)]


class ListParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(10, ge=1, le=100)
    starting_after: str | None = None
    ending_before: str | None = None
