"""PR #565 (2026-06-25) — constant-time dummy-hash on user-not-found.

Pins the timing-equalization guard:

  * verify_password runs against _DUMMY_PASSWORD_HASH on Step 3
    (no DB row), so username enumeration via timing is blocked.
  * Same dummy-verify runs on Step 2 (DB row + NULL hash) so
    "this user has no DB password" doesn't leak either.
  * Step 1 (DB row + real hash) does NOT run the dummy — the real
    verify_password already burns the equivalent CPU.
  * Dummy hash is computed ONCE at module load (not per-request).
  * Graceful degradation: when argon2-cffi is missing,
    _DUMMY_PASSWORD_HASH is None and the dummy-verify is a no-op
    (timing leak remains, auth still works).

Closes the last known-limitation flag from PR #562.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import patch

# ── source pins ────────────────────────────────────────────────────


def test_source_pin_dummy_hash_module_level():
    """The dummy hash MUST be computed ONCE at module load — not
    per-request. Pin the module-level assignment + helper signature."""
    import server.review_server as mod

    src = Path(mod.__file__).read_text()
    assert "def _compute_dummy_hash() -> str | None:" in src
    assert "_DUMMY_PASSWORD_HASH: str | None = _compute_dummy_hash()" in src
    assert "PR #565" in src


def test_source_pin_dummy_verify_helper_present():
    import server.review_server as mod

    src = Path(mod.__file__).read_text()
    assert "def _constant_time_dummy_verify(submitted_pass: str, verify_fn) -> None:" in src


def test_source_pin_both_fallthrough_paths_call_dummy_verify():
    """Both Step 2 (NULL hash branch) AND Step 3 (no DB row
    branch) must call _constant_time_dummy_verify. Pin via the
    count of calls in the file — should be exactly 2 (one per
    branch)."""
    import server.review_server as mod

    src = Path(mod.__file__).read_text()
    # The literal call appears in both branches
    call_count = src.count("_constant_time_dummy_verify(submitted_pass, verify_password)")
    assert call_count == 2, (
        f"expected exactly 2 calls to _constant_time_dummy_verify "
        f"(Step 2 NULL-hash branch + Step 3 no-row branch); got {call_count}"
    )


# ── _compute_dummy_hash unit tests ─────────────────────────────────


def test_compute_dummy_hash_returns_argon2id_format():
    """The dummy hash uses real argon2id — pin the format."""
    import server.review_server as mod

    h = mod._compute_dummy_hash()
    assert h is not None
    assert h.startswith("$argon2id$"), f"expected argon2id format; got {h[:30]}"


def test_compute_dummy_hash_returns_none_on_argon2_import_failure():
    """Graceful degradation when argon2-cffi is missing —
    _compute_dummy_hash returns None and the dummy-verify
    becomes a no-op."""
    import server.review_server as mod

    with patch(
        "genlab_core.auth.passwords.hash_password",
        side_effect=ImportError("argon2-cffi not installed"),
    ):
        assert mod._compute_dummy_hash() is None


def test_module_level_dummy_hash_is_set():
    """At import time _DUMMY_PASSWORD_HASH should be populated
    (argon2-cffi IS installed in the test env). Critical: this
    is the module-level singleton — recomputing per-request
    would defeat the whole 'compute once at startup' design."""
    import server.review_server as mod

    assert mod._DUMMY_PASSWORD_HASH is not None
    assert mod._DUMMY_PASSWORD_HASH.startswith("$argon2id$")


# ── _constant_time_dummy_verify unit tests ─────────────────────────


def test_dummy_verify_invokes_verify_fn():
    """The helper invokes the passed verify_fn with the submitted
    password and the precomputed dummy hash. Pin via mock."""
    import server.review_server as mod

    mock_verify = patch("server.review_server.verify_password", create=True).start()
    try:
        # Direct call (bypass _authenticate so we can isolate)
        mod._constant_time_dummy_verify("any_password", mock_verify)
        mock_verify.assert_called_once_with("any_password", mod._DUMMY_PASSWORD_HASH)
    finally:
        patch.stopall()


def test_dummy_verify_noop_when_dummy_hash_is_none(monkeypatch):
    """When _DUMMY_PASSWORD_HASH is None (argon2-cffi missing at
    load), the helper short-circuits without calling verify_fn."""
    import server.review_server as mod

    monkeypatch.setattr(mod, "_DUMMY_PASSWORD_HASH", None)
    mock_verify = patch("server.review_server.verify_password", create=True).start()
    try:
        mod._constant_time_dummy_verify("any", mock_verify)
        mock_verify.assert_not_called()
    finally:
        patch.stopall()


def test_dummy_verify_swallows_exceptions():
    """verify_fn exceptions during dummy-verify MUST NOT propagate
    — auth flow continues to the env-var fallback regardless."""
    import server.review_server as mod

    def boom(*_args, **_kwargs):
        raise RuntimeError("simulated argon2 failure")

    # Must not raise
    mod._constant_time_dummy_verify("any_password", boom)


# ── integration: dummy-verify fires on Step 2 + Step 3 ────────────


def _patch_env_creds(monkeypatch, user="admin", password="env_pass"):
    """Configure review_server's module-level env-var creds."""
    import server.review_server as mod

    monkeypatch.setattr(mod, "_AUTH_USER", user)
    monkeypatch.setattr(mod, "_AUTH_PASS", password)


def test_step_3_no_user_fires_dummy_verify(monkeypatch):
    """Critical: when get_user_by_username returns None, the
    dummy-verify MUST fire so timing matches Step 1's verify cost.
    Without this, an attacker can enumerate usernames by timing."""
    import server.review_server as mod

    _patch_env_creds(monkeypatch, user="admin", password="env_pass")

    dummy_verify_calls = []

    def tracking_dummy(submitted_pass, verify_fn):
        dummy_verify_calls.append(submitted_pass)

    with (
        patch("genlab_core.auth.users.get_user_by_username", return_value=None),
        patch.object(mod, "_constant_time_dummy_verify", side_effect=tracking_dummy),
    ):
        mod._authenticate("nonexistent_user", "guess_password")

    assert len(dummy_verify_calls) == 1
    assert dummy_verify_calls[0] == "guess_password"


def test_step_2_null_hash_fires_dummy_verify(monkeypatch):
    """When DB row exists but password_hash is NULL (seed shape),
    dummy-verify ALSO fires so 'this user has no DB password'
    doesn't leak via timing."""
    import server.review_server as mod
    from genlab_core.auth.users import User

    _patch_env_creds(monkeypatch, user="admin", password="env_pass")
    user = User(
        id=uuid.uuid4(),
        username="admin",
        display_name="Admin",
        tenant_id=uuid.uuid4(),
        role="admin",
        status="active",
    )

    dummy_verify_calls = []

    def tracking_dummy(submitted_pass, verify_fn):
        dummy_verify_calls.append(submitted_pass)

    with (
        patch("genlab_core.auth.users.get_user_by_username", return_value=user),
        patch("genlab_core.auth.users.get_user_password_hash", return_value=None),
        patch.object(mod, "_constant_time_dummy_verify", side_effect=tracking_dummy),
    ):
        mod._authenticate("admin", "env_pass")

    assert len(dummy_verify_calls) == 1
    assert dummy_verify_calls[0] == "env_pass"


def test_step_1_does_not_fire_dummy_verify(monkeypatch):
    """When the real verify_password runs (Step 1), the dummy is
    NOT invoked — would double the CPU work. Pin so a future
    refactor doesn't accidentally call it on the happy path."""
    import server.review_server as mod
    from genlab_core.auth.users import User

    _patch_env_creds(monkeypatch, user="env_user", password="env_pass")
    user = User(
        id=uuid.uuid4(),
        username="admin",
        display_name="Admin",
        tenant_id=uuid.uuid4(),
        role="admin",
        status="active",
    )

    with (
        patch("genlab_core.auth.users.get_user_by_username", return_value=user),
        patch("genlab_core.auth.users.get_user_password_hash", return_value="$argon2id$..."),
        patch("genlab_core.auth.passwords.verify_password", return_value=True),
        patch("genlab_core.auth.users.update_last_login"),
        patch("genlab_core.auth.passwords.needs_rehash", return_value=False),
        patch.object(mod, "_constant_time_dummy_verify") as mock_dummy,
    ):
        mod._authenticate("admin", "correct_password")

    mock_dummy.assert_not_called()


def test_step_1_failed_verify_does_not_fire_dummy(monkeypatch):
    """Step 1 with WRONG password — verify_password returns False.
    The dummy should ALSO not fire (Step 1 path returns False
    directly without going through the dummy block). Real verify
    already burned the CPU."""
    import server.review_server as mod
    from genlab_core.auth.users import User

    _patch_env_creds(monkeypatch, user="env_user", password="env_pass")
    user = User(
        id=uuid.uuid4(),
        username="admin",
        display_name="Admin",
        tenant_id=uuid.uuid4(),
        role="admin",
        status="active",
    )

    with (
        patch("genlab_core.auth.users.get_user_by_username", return_value=user),
        patch("genlab_core.auth.users.get_user_password_hash", return_value="$argon2id$..."),
        patch("genlab_core.auth.passwords.verify_password", return_value=False),
        patch.object(mod, "_constant_time_dummy_verify") as mock_dummy,
    ):
        result = mod._authenticate("admin", "wrong_password")

    assert result is False
    mock_dummy.assert_not_called()


def test_dummy_verify_does_not_change_auth_outcome(monkeypatch):
    """The dummy-verify result is DISCARDED — auth outcome must
    depend ONLY on the env-var fallback (or Step 1 verify).
    Pin: with correct env creds and a dummy that returns False,
    auth still succeeds."""
    import server.review_server as mod
    from genlab_core.auth.users import User

    _patch_env_creds(monkeypatch, user="admin", password="env_pass")
    user = User(
        id=uuid.uuid4(),
        username="admin",
        display_name="Admin",
        tenant_id=uuid.uuid4(),
        role="admin",
        status="active",
    )

    with (
        patch("genlab_core.auth.users.get_user_by_username", return_value=user),
        patch("genlab_core.auth.users.get_user_password_hash", return_value=None),  # Step 2
        # Dummy verify returns whatever — irrelevant to the outcome
        patch.object(mod, "_constant_time_dummy_verify", return_value=False),
    ):
        # Correct env-var creds → auth succeeds despite dummy verify
        assert mod._authenticate("admin", "env_pass") is True
