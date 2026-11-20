"""Python client for the Cleartill payments API.

client = cleartill.Client("sk_test_...", base_url="http://localhost:8000")
pi = client.payment_intents.create(amount=2000, currency="usd",
                                   payment_method="pm_card_visa", confirm=True)
"""

from cleartill._client import VERSION as __version__
from cleartill._client import Client
from cleartill._encode import encode
from cleartill._errors import (
    APIConnectionError,
    APIError,
    AuthenticationError,
    CardError,
    CleartillError,
    CleartillPermissionError,
    IdempotencyError,
    InvalidRequestError,
    RateLimitError,
    SignatureVerificationError,
)
from cleartill._errors import (
    PermissionError as PermissionError,  # public, but kept out of __all__ (builtin name)
)
from cleartill._webhook import Webhook

__all__ = [
    "APIConnectionError",
    "APIError",
    "AuthenticationError",
    "CardError",
    "CleartillError",
    "CleartillPermissionError",
    "Client",
    "IdempotencyError",
    "InvalidRequestError",
    "RateLimitError",
    "SignatureVerificationError",
    "Webhook",
    "__version__",
    "encode",
]
