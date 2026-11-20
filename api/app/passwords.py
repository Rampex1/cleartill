"""Password hashing with stdlib scrypt (no extra dependency)."""

import base64
import binascii
import hashlib
import hmac
import secrets

# OWASP-equivalent to its N=2^17 minimum at 16 MB per hash (N=2^17 would need 128 MB per login).
N, R, P, DKLEN = 2**14, 8, 5, 32
_MAXMEM = 64 * 1024 * 1024


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=N, r=R, p=P, dklen=DKLEN, maxmem=_MAXMEM
    )
    return f"scrypt${N}${R}${P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, n, r, p, salt, digest = stored.split("$")
        if algorithm != "scrypt":
            return False
        expected = base64.b64decode(digest, validate=True)
        actual = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt, validate=True),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
            maxmem=_MAXMEM,
        )
    except (ValueError, binascii.Error):
        return False
    return hmac.compare_digest(actual, expected)


def needs_rehash(stored: str) -> bool:
    """True when `stored` was made with weaker parameters than today's (rehash at login)."""
    return not stored.startswith(f"scrypt${N}${R}${P}$")


# Verified against when the email is unknown, so login takes the same time either way.
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))
