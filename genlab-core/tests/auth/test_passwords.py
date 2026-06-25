"""PR #561 (2026-06-25) — password hashing primitives.

Pins:

  * hash_password produces self-describing argon2id strings
  * verify_password is constant-time (never raises) and returns
    False on EVERY failure path
  * needs_rehash returns False conservatively on bad input
  * DoS guard rejects >MAX_PASSWORD_LEN plaintext
  * The hash output uses the OWASP-recommended argon2id variant
"""

from __future__ import annotations

import pytest

# ── module structure pins ──────────────────────────────────────────


def test_public_surface_importable():
    from genlab_core.auth.passwords import (
        MAX_PASSWORD_LEN,
        hash_password,
        needs_rehash,
        verify_password,
    )

    assert callable(hash_password)
    assert callable(verify_password)
    assert callable(needs_rehash)
    assert isinstance(MAX_PASSWORD_LEN, int)


def test_max_password_len_is_reasonable():
    """1KB is the agreed DoS guard. Real passwords are <100 chars.
    Pin the constant so a future refactor doesn't accidentally
    raise it (which would defeat the guard) or lower it below 256
    (which would break edge-case very-long passphrases)."""
    from genlab_core.auth.passwords import MAX_PASSWORD_LEN

    assert MAX_PASSWORD_LEN == 1024


# ── happy path ─────────────────────────────────────────────────────


def test_hash_produces_argon2id_format():
    """The hash string MUST start with $argon2id$ — OWASP's
    recommended variant. argon2i (data-independent only) and
    argon2d (data-dependent only) are weaker; the 'id' blend is
    the strongest of the three."""
    from genlab_core.auth.passwords import hash_password

    h = hash_password("correct horse battery staple")
    assert h.startswith("$argon2id$"), f"expected argon2id variant; got: {h[:30]}"


def test_hash_includes_owasp_baseline_params():
    """Pin the OWASP-baseline argon2id parameters in the encoded
    hash string. argon2-cffi uses library defaults; if the
    underlying library lowers them in a future version, this test
    catches the regression. m=65536 (64 MiB) is the minimum OWASP
    recommends."""
    from genlab_core.auth.passwords import hash_password

    h = hash_password("any input")
    # Format: $argon2id$v=19$m=65536,t=3,p=4$<salt>$<hash>
    assert "m=65536" in h, "argon2id memory_cost dropped below OWASP baseline"
    assert "t=3" in h, "argon2id time_cost dropped below OWASP baseline"
    assert "p=4" in h, "argon2id parallelism dropped below OWASP baseline"


def test_hash_produces_different_output_for_same_input():
    """Each hash should embed a random salt → same plaintext yields
    different hashes. Critical for resisting rainbow-table attacks."""
    from genlab_core.auth.passwords import hash_password

    h1 = hash_password("same_password")
    h2 = hash_password("same_password")
    assert h1 != h2, "hash_password must use random salt per call"


def test_verify_correct_password_returns_true():
    from genlab_core.auth.passwords import hash_password, verify_password

    h = hash_password("the_real_password")
    assert verify_password("the_real_password", h) is True


def test_verify_wrong_password_returns_false():
    from genlab_core.auth.passwords import hash_password, verify_password

    h = hash_password("the_real_password")
    assert verify_password("wrong_guess", h) is False


def test_needs_rehash_returns_false_for_default_hash():
    """A freshly-hashed password with current defaults should NOT
    need a rehash. If this fails, the library bumped its defaults
    and we should mention it in the docstring."""
    from genlab_core.auth.passwords import hash_password, needs_rehash

    h = hash_password("any")
    assert needs_rehash(h) is False


# ── DoS guards ─────────────────────────────────────────────────────


def test_hash_rejects_empty_plaintext():
    from genlab_core.auth.passwords import hash_password

    with pytest.raises(ValueError, match="non-empty"):
        hash_password("")


def test_hash_rejects_over_max_length():
    from genlab_core.auth.passwords import MAX_PASSWORD_LEN, hash_password

    with pytest.raises(ValueError, match="MAX_PASSWORD_LEN"):
        hash_password("a" * (MAX_PASSWORD_LEN + 1))


def test_hash_accepts_exactly_max_length():
    """Boundary pin — MAX_PASSWORD_LEN is INCLUSIVE."""
    from genlab_core.auth.passwords import MAX_PASSWORD_LEN, hash_password

    h = hash_password("a" * MAX_PASSWORD_LEN)
    assert h.startswith("$argon2id$")


# ── verify fail-modes (never raises) ───────────────────────────────


def test_verify_empty_plaintext_returns_false_not_raises():
    from genlab_core.auth.passwords import hash_password, verify_password

    h = hash_password("anything")
    assert verify_password("", h) is False


def test_verify_empty_hash_returns_false():
    from genlab_core.auth.passwords import verify_password

    assert verify_password("password", "") is False


def test_verify_none_inputs_return_false():
    from genlab_core.auth.passwords import verify_password

    # Type ignores — runtime should NOT raise even on bad types
    assert verify_password(None, None) is False  # type: ignore[arg-type]
    assert verify_password("password", None) is False  # type: ignore[arg-type]


def test_verify_malformed_hash_returns_false_not_raises():
    """An auth caller branching on the bool must NEVER see an
    exception — malformed hashes (corruption, broken write path)
    must convert to False. WARNING is logged but no raise."""
    from genlab_core.auth.passwords import verify_password

    assert verify_password("password", "not-an-argon2-hash") is False
    assert verify_password("password", "$argon2id$nonsense") is False


def test_verify_over_max_length_returns_false_not_raises():
    """The DoS guard on hash_password is mirrored on verify — a
    hostile login attempt with a megabyte password shouldn't even
    invoke argon2."""
    from genlab_core.auth.passwords import MAX_PASSWORD_LEN, hash_password, verify_password

    h = hash_password("ok")
    assert verify_password("a" * (MAX_PASSWORD_LEN + 1), h) is False


# ── needs_rehash fail-modes ────────────────────────────────────────


def test_needs_rehash_empty_returns_false():
    from genlab_core.auth.passwords import needs_rehash

    assert needs_rehash("") is False


def test_needs_rehash_malformed_returns_false():
    """Conservative: an unparseable hash shouldn't trigger a
    rehash attempt because the old hash can't be reused anyway."""
    from genlab_core.auth.passwords import needs_rehash

    assert needs_rehash("garbage") is False
