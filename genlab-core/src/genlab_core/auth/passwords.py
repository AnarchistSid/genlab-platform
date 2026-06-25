"""Password hashing — argon2id with secure defaults (PR #561, 2026-06-25).

Step 3a of the SaaS multi-tenancy dependency order. PR #559 added
the users table with a nullable password_hash column; this module
adds the hash/verify primitives so the column can actually carry a
secret. Authentication-path integration is a separate PR (still
opt-in env-var auth as of #561).

## Why argon2 (not bcrypt / scrypt / PBKDF2)

argon2id is the winner of the Password Hashing Competition (2015)
and the current OWASP recommendation. It defends against:

  * GPU attacks  — argon2id is memory-hard (m_cost ≥ 64 MiB
                   recommended; the GPU's parallelism doesn't help
                   when each work-unit needs 64 MiB)
  * Side-channel — the "id" variant blends data-dependent +
                   data-independent indexing (vs argon2i alone,
                   which is data-independent but weaker against
                   tradeoff attacks)
  * Rainbow tables — per-hash random salt baked into the encoded
                     string (no separate salt column needed)

argon2-cffi (the standard binding) handles encoding, parsing,
parameter negotiation, and constant-time verification. We use its
``PasswordHasher`` with library defaults — the maintainers track
the current parameter recommendations more carefully than we can.

## Public surface

  hash_password(plaintext: str) -> str
    Encode a fresh password. Returns the self-describing argon2id
    hash string (~96 chars). Each call produces a different output
    even for the same plaintext (random salt).

  verify_password(plaintext: str, hash_str: str) -> bool
    Constant-time check. Returns False on any failure (wrong
    password, malformed hash, ImportError on optional dep).
    NEVER raises — auth-path callers can branch on the bool
    without try/except.

  needs_rehash(hash_str: str) -> bool
    True when the hash was made with weaker parameters than
    current defaults. The auth path should rehash + persist on
    successful login when this returns True (transparent param
    upgrades over time).

## Failure modes

  hash_password    — raises on empty / overly-long plaintext (>=1
                     KB is excessive; protects against memory
                     DoS via huge-input hash attempts)
  verify_password  — returns False on EVERY failure path. Never
                     raises. argon2 itself raises on malformed
                     hashes; we catch and convert to False.
  needs_rehash     — returns False on any failure (conservative;
                     a malformed hash shouldn't trigger a rehash
                     attempt because the old hash can't be reused
                     anyway).

## Constants

Caller-visible:
  MAX_PASSWORD_LEN = 1024  — DoS guard. Real passwords are
                              <100 chars; >1KB is hostile input.

Internal:
  Library defaults from argon2-cffi 23.1 (as of 2026-06-25):
    time_cost = 3
    memory_cost = 64 MiB
    parallelism = 4
    hash_len = 32
    salt_len = 16
  These are the OWASP-recommended baseline for argon2id.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# DoS guard. Real passwords are <100 chars; 1KB is comfortable headroom.
# Hashing a multi-MB blob would burn server memory + CPU for no benefit.
MAX_PASSWORD_LEN = 1024


def _hasher():
    """Lazy-import PasswordHasher so the module is importable even
    when argon2-cffi isn't installed (graceful degradation in
    contexts that don't actually call hash/verify — e.g. unit tests
    for unrelated modules)."""
    from argon2 import PasswordHasher

    return PasswordHasher()


def hash_password(plaintext: str) -> str:
    """Hash a fresh password. Returns the argon2id encoded string.

    Raises:
      ValueError — empty plaintext or > MAX_PASSWORD_LEN
      ImportError — argon2-cffi not installed (propagated so the
                    caller knows the optional dep is missing;
                    silently returning would let the system run
                    without password hashing which would be much
                    worse than crashing loudly)
    """
    if not plaintext:
        raise ValueError("password must be non-empty")
    if len(plaintext) > MAX_PASSWORD_LEN:
        raise ValueError(
            f"password exceeds MAX_PASSWORD_LEN ({MAX_PASSWORD_LEN}); "
            "real passwords are <100 chars — refusing to hash hostile input"
        )
    return _hasher().hash(plaintext)


def verify_password(plaintext: str, hash_str: str) -> bool:
    """Constant-time check. Returns False on EVERY failure path —
    wrong password, malformed hash, missing argon2-cffi, None
    inputs, empty inputs.

    The auth caller's pattern is:

        if verify_password(submitted, user_row.password_hash):
            ...

    No try/except needed — that's why this helper exists.
    """
    if not plaintext or not hash_str:
        return False
    if len(plaintext) > MAX_PASSWORD_LEN:
        # Don't even attempt — same DoS guard as hash_password.
        return False
    try:
        from argon2.exceptions import VerifyMismatchError

        _hasher().verify(hash_str, plaintext)
        return True
    except VerifyMismatchError:
        # Wrong password — the normal failure path. argon2 made the
        # check constant-time relative to mismatches, so this branch
        # leaks nothing about which bytes differed.
        return False
    except Exception as exc:  # noqa: BLE001 — never raise from auth check
        # Malformed hash, ImportError, ANY other failure → False.
        # Logged at WARNING because malformed hashes in the users
        # table indicate corruption or a broken write path.
        logger.warning("[passwords] verify failed (treating as mismatch): %s", exc)
        return False


def needs_rehash(hash_str: str) -> bool:
    """True when the hash was made with weaker parameters than
    current defaults. The auth path should rehash + persist on
    next successful login when this returns True — transparent
    parameter upgrades over time.

    Conservative: returns False on ANY failure (malformed hash,
    missing argon2-cffi) because attempting a rehash when we can't
    parse the existing hash is meaningless.
    """
    if not hash_str:
        return False
    try:
        return _hasher().check_needs_rehash(hash_str)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[passwords] check_needs_rehash failed: %s", exc)
        return False
