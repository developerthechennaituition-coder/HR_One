import hashlib
import hmac
import secrets


_ITERATIONS = 120_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    password_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("ascii"),
        _ITERATIONS,
    ).hex()
    return f"{salt}${password_hash}"


def verify_password(password: str, stored_hash: str | None) -> bool:
    if not stored_hash:
        return False
    salt, separator, expected_hash = stored_hash.partition("$")
    if not separator:
        return False
    candidate_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("ascii"),
        _ITERATIONS,
    ).hex()
    return hmac.compare_digest(candidate_hash, expected_hash)
