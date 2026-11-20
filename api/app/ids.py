import secrets
import string

_ALPHABET = string.digits + string.ascii_letters


def random_string(length: int) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def new_id(prefix: str) -> str:
    """Stripe-style object id: prefix + 24 base62 chars (~143 bits)."""
    return prefix + random_string(24)
