"""Password hashing with scrypt from the standard library. Pure functions; nothing is logged.

A stored hash is "scrypt$N$r$p$salt$key" (salt and key in URL-safe base64). The parameters are read back from the stored hash, so they
can be raised later without breaking existing accounts. Comparison is constant-time.
"""

import base64
import hashlib
import hmac
import secrets

N, R, P = 2 ** 14, 8, 1           # the minimum allowed; raising them only makes new hashes slower to compute
SALT_BYTES, KEY_BYTES = 16, 32
_MAXMEM = 64 * 1024 * 1024


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _derive(password: str, salt: bytes, n: int, r: int, p: int, length: int) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=length, maxmem=_MAXMEM)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    return f"scrypt${N}${R}${P}${_b64(salt)}${_b64(_derive(password, salt, N, R, P, KEY_BYTES))}"


def verify_password(password: str, stored: str | None) -> bool:
    """True only if `stored` is a well-formed hash at or above the minimum parameters and `password` matches it."""
    try:
        scheme, n, r, p, salt, key = (stored or "").split("$")
        n, r, p = int(n), int(r), int(p)
        if scheme != "scrypt" or n < N or r < R or p < P:
            return False
        expected = _unb64(key)
        return hmac.compare_digest(_derive(password, _unb64(salt), n, r, p, len(expected)), expected)
    except (ValueError, TypeError):
        return False


# Checked when the email is unknown, so a wrong email takes as long as a wrong password.
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))
