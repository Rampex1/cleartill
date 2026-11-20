"""HTTP client: auth, form encoding, idempotency keys, retries, error mapping."""

import math
import random
import time
import uuid
from collections.abc import Iterator
from urllib.parse import quote

import httpx2

from cleartill._encode import encode
from cleartill._errors import APIConnectionError, APIError, error_for

VERSION = "0.1.0"
RETRY_STATUSES = {409, 429}
MAX_BACKOFF = 8.0


class Client:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "http://localhost:8000",
        max_retries: int = 2,
        timeout: float = 30.0,
        http_client: httpx2.Client | None = None,
    ):
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        # `timeout` applies only to the client created here; a passed-in client keeps its own.
        self._owns_http = http_client is None
        self._http = http_client or httpx2.Client(timeout=timeout)
        self._sleep = time.sleep
        self.customers = _Resource(self, "customers", "crudl")
        self.payment_methods = _Resource(self, "payment_methods", "cr")
        self.payment_intents = _PaymentIntents(self, "payment_intents", "crul")
        self.charges = _Resource(self, "charges", "rl")
        self.refunds = _Resource(self, "refunds", "crl")
        self.balance = _Balance(self)
        self.balance_transactions = _Resource(self, "balance_transactions", "rl")
        self.events = _Resource(self, "events", "rl")
        self.webhook_endpoints = _Resource(self, "webhook_endpoints", "crudl")
        self.checkout = _Namespace(sessions=_Resource(self, "checkout/sessions", "crl"))

    def close(self) -> None:
        """Close the HTTP connection pool (only one this client created)."""
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def request(
        self, method: str, path: str, params: dict | None = None, idempotency_key: str | None = None
    ) -> dict:
        pairs = encode(params or {})
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": f"cleartill-python/{VERSION}",
        }
        if method == "POST":
            # One key per call, reused on every retry, so a retried POST can't act twice.
            headers["Idempotency-Key"] = idempotency_key or str(uuid.uuid4())
        url = f"{self.base_url}{path}"
        for attempt in range(self.max_retries + 1):
            last = attempt == self.max_retries
            try:
                if method == "POST":
                    response = self._http.request(method, url, data=dict(pairs), headers=headers)
                else:
                    response = self._http.request(method, url, params=pairs, headers=headers)
            except httpx2.TransportError as exc:
                # POST is keyed and GET is read-only, so both retry safely. A DELETE without a
                # key retries only when the request never left (connect failed).
                safe = method != "DELETE" or isinstance(exc, httpx2.ConnectError)
                if last or not safe:
                    raise APIConnectionError(f"Could not reach Cleartill: {exc}") from exc
                self._sleep(self._backoff(attempt, None))
                continue
            status = response.status_code
            request_id = response.headers.get("Request-Id")
            if 200 <= status < 300:
                try:
                    return response.json()
                except ValueError:
                    raise APIError(
                        "Cleartill returned a response that isn't JSON.",
                        http_status=status,
                        request_id=request_id,
                    ) from None
            # 5xx may have run a DELETE; 409/429 mean it didn't (lock timeout, rate limit).
            retryable = status in RETRY_STATUSES or (status >= 500 and method != "DELETE")
            if not last and retryable:
                self._sleep(self._backoff(attempt, response.headers.get("Retry-After")))
                continue
            try:
                body = response.json()
            except ValueError:
                body = {}
            raise error_for(status, body, request_id)
        raise AssertionError("unreachable")

    @staticmethod
    def _backoff(attempt: int, retry_after: str | None) -> float:
        if retry_after is not None:
            try:
                seconds = float(retry_after)
            except ValueError:
                seconds = math.nan
            if math.isfinite(seconds):
                return min(MAX_BACKOFF, max(0.0, seconds))
        return random.uniform(0, min(MAX_BACKOFF, 0.5 * 2**attempt))  # full jitter


class _Namespace:
    def __init__(self, **resources):
        self.__dict__.update(resources)


class _Resource:
    """`ops`: c(reate) r(etrieve) u(pdate) d(elete) l(ist)."""

    def __init__(self, client: Client, name: str, ops: str):
        self._client, self._path, self._ops = client, f"/v1/{name}", ops

    def _need(self, op: str) -> None:
        if op not in self._ops:
            raise AttributeError(f"{self._path} doesn't support this operation")

    def _item(self, id: str) -> str:  # noqa: A002
        # Quote ids so a crafted value ("../webhook_endpoints/x", "a?b") can't change the path.
        return f"{self._path}/{quote(str(id), safe='')}"

    def create(self, *, idempotency_key: str | None = None, **params) -> dict:
        self._need("c")
        return self._client.request("POST", self._path, params, idempotency_key)

    def retrieve(self, id: str, /, **params) -> dict:  # noqa: A002
        self._need("r")
        return self._client.request("GET", self._item(id), params)

    def update(self, id: str, /, *, idempotency_key: str | None = None, **params) -> dict:  # noqa: A002
        self._need("u")
        return self._client.request("POST", self._item(id), params, idempotency_key)

    def delete(self, id: str, /) -> dict:  # noqa: A002
        self._need("d")
        return self._client.request("DELETE", self._item(id))

    def list(self, **params) -> dict:
        self._need("l")
        return self._client.request("GET", self._path, params)

    def list_all(self, **params) -> Iterator[dict]:
        """Every object, newest first, following `has_more` with `starting_after`."""
        if params.get("ending_before"):
            raise ValueError("list_all pages forward; don't combine it with ending_before")
        while True:
            page = self.list(**params)
            yield from page["data"]
            if not page.get("has_more") or not page["data"]:
                return
            params = {**params, "starting_after": page["data"][-1]["id"]}


class _PaymentIntents(_Resource):
    def _action(self, id: str, action: str, idempotency_key: str | None, params: dict) -> dict:  # noqa: A002
        path = f"{self._item(id)}/{action}"
        return self._client.request("POST", path, params, idempotency_key)

    def confirm(self, id: str, /, *, idempotency_key: str | None = None, **params) -> dict:  # noqa: A002
        return self._action(id, "confirm", idempotency_key, params)

    def capture(self, id: str, /, *, idempotency_key: str | None = None, **params) -> dict:  # noqa: A002
        return self._action(id, "capture", idempotency_key, params)

    def cancel(self, id: str, /, *, idempotency_key: str | None = None, **params) -> dict:  # noqa: A002
        return self._action(id, "cancel", idempotency_key, params)


class _Balance:
    def __init__(self, client: Client):
        self._client = client

    def retrieve(self) -> dict:
        return self._client.request("GET", "/v1/balance")
