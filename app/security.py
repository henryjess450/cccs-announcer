"""Password hashing, tokens, and constant-time comparison.

No cryptography dependency. Python's standard library ships scrypt (via
OpenSSL), which is a memory-hard password hash and the right tool here. That
keeps the dependency list at four packages, which matters for a machine a
school has to keep running for years.

Stored hash format (one string, self-describing so parameters can change later
without invalidating existing passwords):

    scrypt$n$r$p$<salt base64>$<hash base64>
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import Tuple

# ~16 MB and roughly 50-100 ms per hash on a modest office PC. Slow enough to
# make guessing expensive, fast enough that signing in feels instant.
SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
KEY_BYTES = 32

TOKEN_BYTES = 32


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("Password must not be empty.")
    salt = secrets.token_bytes(SALT_BYTES)
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt,
        n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=KEY_BYTES,
        maxmem=64 * 1024 * 1024,
    )
    return "scrypt${}${}${}${}${}".format(
        SCRYPT_N, SCRYPT_R, SCRYPT_P,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(derived).decode("ascii"),
    )


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check. Returns False rather than raising on a bad record."""
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        derived = hashlib.scrypt(
            password.encode("utf-8"), salt=salt,
            n=int(n), r=int(r), p=int(p), dklen=len(expected),
            maxmem=64 * 1024 * 1024,
        )
    except (ValueError, TypeError, MemoryError):
        return False
    return hmac.compare_digest(derived, expected)


def new_token() -> str:
    """A session or CSRF token. URL-safe, 256 bits of entropy."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def token_fingerprint(token: str) -> str:
    """What we store for a session token.

    Sessions are looked up by the SHA-256 of the cookie value, never by the
    value itself. Someone who reads the database still cannot use the sessions
    in it.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_match(left: str, right: str) -> bool:
    return hmac.compare_digest(str(left or ""), str(right or ""))


def generate_password(words: int = 3) -> str:
    """A readable initial password for a new account.

    Word-shaped so it can be read out over the phone or written on a slip of
    paper without ambiguity, and long enough to be safe until it is changed.
    """
    alphabet = "abcdefghijkmnopqrstuvwxyz"      # no l
    digits = "23456789"                          # no 0/1
    parts = ["".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(words)]
    return "-".join(parts) + "-" + "".join(secrets.choice(digits) for _ in range(2))


def split_stored(stored: str) -> Tuple[str, ...]:
    """Exposed for tests: the parts of a stored hash."""
    return tuple(stored.split("$"))
