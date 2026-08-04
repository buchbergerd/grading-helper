"""Password hashing and policy (SPECIFICATION.md §3).

argon2id via ``argon2-cffi`` with the library's current default parameters. The parameters are
embedded in every stored hash, so raising them later is a one-line change here plus the
:func:`needs_rehash` path below, which transparently upgrades a user's stored hash the next time
they log in successfully.

Policy messages returned by :func:`validate_password_strength` are German — they are shown
verbatim in the UI (CLAUDE.md: everything user-facing is German).
"""

from __future__ import annotations

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

#: Minimum password length. This is an internal departmental tool where every account is either
#: admin-created or self-registered via an admin-issued, reusable invitation code (§3) — never
#: fully public signup — so length is the only rule: composition rules ("must contain a digit")
#: push users towards predictable substitutions without adding real entropy.
MIN_PASSWORD_LENGTH = 8

_hasher = PasswordHasher()

#: A hash of a throwaway random secret, used by the login route to spend the same amount of CPU
#: on an unknown username as on a known one, so response timing does not distinguish the two.
#: Computed once at import time; nothing ever verifies successfully against it.
DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(32))


def hash_password(password: str) -> str:
    """Return an argon2id hash (salt and parameters included in the returned string)."""
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """True if ``password`` matches ``password_hash``.

    Returns ``False`` — never raises — for a wrong password *and* for a stored value that is not
    a parseable argon2 hash (e.g. a placeholder left by a fixture or a half-finished migration).
    A malformed hash must fail closed, not 500.
    """
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True if ``password_hash`` was made with parameters weaker than the current defaults.

    Call only after :func:`verify_password` succeeded — the plaintext is needed to produce the
    replacement hash. A malformed hash reports ``True`` so it gets replaced rather than kept.
    """
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def validate_password_strength(password: str) -> list[str]:
    """Validate a new password; return German error messages (empty list = acceptable)."""
    errors: list[str] = []
    if len(password) < MIN_PASSWORD_LENGTH:
        errors.append(f"Das Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen lang sein.")
    if password.strip() == "":
        errors.append("Das Passwort darf nicht nur aus Leerzeichen bestehen.")
    return errors
