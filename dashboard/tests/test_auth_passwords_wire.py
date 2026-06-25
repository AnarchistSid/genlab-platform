"""PR #562 (2026-06-25) — auth path consumes password primitives.

Pins the 3-step resolver _authenticate(user, pass):

  Step 1. DB users row exists + password_hash non-NULL → verify_password
          is SOLE check. update_last_login fires on success.
  Step 2. DB users row exists + password_hash NULL → env-var fallback
          (PR #559 seed shape: admin row exists, env-var still owns auth).
  Step 3. DB users row absent / DB down → env-var fallback
          (legacy operators not yet in DB; pre-PR-562 behavior bit-for-bit).

Plus: suspended/deactivated user status NEVER grants auth, even with
the right password. Status enforcement at auth time mirrors the
"status enforced at write time" principle.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import patch

# ── source pins ────────────────────────────────────────────────────


def test_source_pin_authenticate_helper_present():
    """The _authenticate resolver exists with the expected name +
    PR anchor. Pin so future refactors that rename or inline it
    surface in tests."""
    import server.review_server as mod

    src = Path(mod.__file__).read_text()
    assert "def _authenticate(submitted_user: str, submitted_pass: str) -> bool:" in src
    assert "PR #562" in src


def test_source_pin_form_post_uses_authenticate(monkeypatch):
    """The form-POST login path MUST use the same _authenticate
    helper as basic-auth — pin so a future refactor doesn't add
    a third inline password check."""
    import server.review_server as mod

    src = Path(mod.__file__).read_text()
    # The form-POST branch invokes _authenticate(username, password)
    assert "if _authenticate(username, password):" in src


def test_source_pin_basic_auth_uses_authenticate():
    """The basic-auth header path also routes through _authenticate."""
    import server.review_server as mod

    src = Path(mod.__file__).read_text()
    assert 'if auth and _authenticate(auth.username or "", auth.password or ""):' in src


# ── behavioral pins ────────────────────────────────────────────────


def _patch_env_creds(monkeypatch, user="admin", password="env_pass"):
    """Configure review_server's module-level env-var creds without
    re-importing the module (which would also re-init Flask app)."""
    import server.review_server as mod

    monkeypatch.setattr(mod, "_AUTH_USER", user)
    monkeypatch.setattr(mod, "_AUTH_PASS", password)


# ── Step 1: DB password wins, env-var NOT consulted ───────────────


def test_step_1_db_password_correct_grants_auth(monkeypatch):
    """When the user is in DB with a real password_hash AND the
    submitted password verifies, auth succeeds. update_last_login
    fires on success (best-effort)."""
    import server.review_server as mod
    from genlab_core.auth.users import User

    _patch_env_creds(monkeypatch, user="env_only_user", password="env_pass")
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
        patch("genlab_core.auth.users.update_last_login") as mock_login,
    ):
        result = mod._authenticate("admin", "the_real_password")
    assert result is True
    mock_login.assert_called_once_with(user.id)


def test_step_1_db_password_wrong_denies_auth_no_env_fallback(monkeypatch):
    """When the DB row has a real password but the submitted
    password doesn't verify, auth fails — even if the env-var
    values would match. The DB is the source of truth once a
    password is set; falling back to env would be a bypass."""
    import server.review_server as mod
    from genlab_core.auth.users import User

    # Set env-vars to match the SUBMITTED creds — proving they're
    # ignored when the DB row has a real hash.
    _patch_env_creds(monkeypatch, user="admin", password="submitted_pass")
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
    ):
        result = mod._authenticate("admin", "submitted_pass")
    assert result is False


# ── Step 2: DB row exists, password_hash NULL → env-var fallback ──


def test_step_2_db_row_no_hash_falls_through_to_env(monkeypatch):
    """The PR #559 seed pattern: admin row exists with NULL
    password_hash. The env-var REVIEW_AUTH_PASS still owns
    authentication. This preserves the pre-PR-562 admin login flow."""
    import server.review_server as mod
    from genlab_core.auth.users import User

    _patch_env_creds(monkeypatch, user="admin", password="env_pass_value")
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
        patch("genlab_core.auth.users.get_user_password_hash", return_value=None),  # NULL
    ):
        # Correct env-var creds → auth succeeds via fallback
        assert mod._authenticate("admin", "env_pass_value") is True
        # Wrong creds → auth fails (env-var is consulted, doesn't match)
        assert mod._authenticate("admin", "wrong") is False


# ── Step 3: DB row absent → env-var fallback (legacy behavior) ───


def test_step_3_db_row_absent_falls_through_to_env(monkeypatch):
    """Legacy operator not yet in the users table. Auth falls
    through to env-var check — bit-for-bit pre-PR-562 behavior."""
    import server.review_server as mod

    _patch_env_creds(monkeypatch, user="legacy_op", password="legacy_pass")

    with patch("genlab_core.auth.users.get_user_by_username", return_value=None):
        assert mod._authenticate("legacy_op", "legacy_pass") is True
        assert mod._authenticate("legacy_op", "wrong") is False
        assert mod._authenticate("other_user", "legacy_pass") is False


def test_step_3_db_unavailable_falls_through_to_env(monkeypatch):
    """When the DB is unavailable (DSN unset, connection error),
    get_user_by_username returns None and auth falls through to
    env-var. Cold-start safety: deploying this PR before the
    migration applied (or with DB down) doesn't lock out the admin."""
    import server.review_server as mod

    _patch_env_creds(monkeypatch, user="admin", password="env_pass")
    # Simulate fail-close (DB down)
    with patch("genlab_core.auth.users.get_user_by_username", return_value=None):
        assert mod._authenticate("admin", "env_pass") is True


# ── Suspended / deactivated status NEVER grants auth ──────────────


def test_suspended_user_denied_even_with_correct_password(monkeypatch):
    """Status check fires BEFORE password verification. A suspended
    operator can't log in even with the right password. Future
    re-activation via admin UI flips status back."""
    import server.review_server as mod
    from genlab_core.auth.users import User

    _patch_env_creds(monkeypatch, user="admin", password="env_pass")
    user = User(
        id=uuid.uuid4(),
        username="admin",
        display_name="Admin",
        tenant_id=uuid.uuid4(),
        role="admin",
        status="suspended",
    )

    with (
        patch("genlab_core.auth.users.get_user_by_username", return_value=user),
        # verify_password would say True if it ran — but the status
        # check short-circuits before we get there.
        patch("genlab_core.auth.users.get_user_password_hash", return_value="$argon2id$..."),
        patch("genlab_core.auth.passwords.verify_password", return_value=True),
    ):
        assert mod._authenticate("admin", "correct_password") is False


def test_deactivated_user_denied(monkeypatch):
    """Same as suspended — deactivated → never grants auth."""
    import server.review_server as mod
    from genlab_core.auth.users import User

    _patch_env_creds(monkeypatch, user="admin", password="env_pass")
    user = User(
        id=uuid.uuid4(),
        username="admin",
        display_name="Admin",
        tenant_id=uuid.uuid4(),
        role="admin",
        status="deactivated",
    )

    with (
        patch("genlab_core.auth.users.get_user_by_username", return_value=user),
        patch("genlab_core.auth.users.get_user_password_hash", return_value="$argon2id$..."),
        patch("genlab_core.auth.passwords.verify_password", return_value=True),
    ):
        assert mod._authenticate("admin", "correct_password") is False


# ── Edge cases ──────────────────────────────────────────────────────


def test_empty_username_returns_false_no_db_call():
    """Defensive short-circuit: empty username → False, no DB
    roundtrip. Saves a wasted connect for hostile / bot login attempts."""
    import server.review_server as mod

    with patch("genlab_core.auth.users.get_user_by_username") as mock_lookup:
        assert mod._authenticate("", "anything") is False
    mock_lookup.assert_not_called()


def test_import_error_falls_back_to_env_var_only(monkeypatch):
    """If genlab_core.auth.users / .passwords can't be imported
    (running against an old core), auth falls back to env-var
    only. Pin via patching sys.modules to None — same defensive
    pattern as PR #560."""
    import server.review_server as mod

    _patch_env_creds(monkeypatch, user="admin", password="env_pass")
    with patch.dict(
        "sys.modules",
        {"genlab_core.auth.passwords": None, "genlab_core.auth.users": None},
    ):
        # Env-var path still works — no crash
        assert mod._authenticate("admin", "env_pass") is True
        assert mod._authenticate("admin", "wrong") is False


def test_update_last_login_failure_doesnt_block_auth(monkeypatch):
    """update_last_login is best-effort — if the timestamp write
    fails (DB hiccup, network blip), auth must STILL succeed.
    Critical: a transient DB issue shouldn't lock everyone out."""
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
        # update_last_login returns False (write failed) — must not block
        patch("genlab_core.auth.users.update_last_login", return_value=False),
    ):
        assert mod._authenticate("admin", "correct") is True


# ── _env_var_auth_match isolated ───────────────────────────────────


def test_rotation_fires_when_needs_rehash_true(monkeypatch):
    """PR #563: on successful DB-password auth, if needs_rehash
    returns True, the resolver re-hashes via set_password. Pin
    the rotation is invoked with the verified plaintext + user.id.
    """
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
        patch("genlab_core.auth.users.get_user_password_hash", return_value="$argon2id$weak..."),
        patch("genlab_core.auth.passwords.verify_password", return_value=True),
        patch("genlab_core.auth.users.update_last_login"),
        patch("genlab_core.auth.passwords.needs_rehash", return_value=True),
        patch("genlab_core.auth.users.set_password", return_value=True) as mock_set,
    ):
        assert mod._authenticate("admin", "correct_password") is True
    mock_set.assert_called_once_with(user.id, "correct_password")


def test_rotation_skipped_when_needs_rehash_false(monkeypatch):
    """When the stored hash uses current params (the common case),
    rotation is a no-op — set_password NOT called. Pin so a future
    refactor doesn't accidentally re-hash on every login (which
    would defeat argon2's per-login work-factor design)."""
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
        patch("genlab_core.auth.users.get_user_password_hash", return_value="$argon2id$current..."),
        patch("genlab_core.auth.passwords.verify_password", return_value=True),
        patch("genlab_core.auth.users.update_last_login"),
        patch("genlab_core.auth.passwords.needs_rehash", return_value=False),
        patch("genlab_core.auth.users.set_password") as mock_set,
    ):
        assert mod._authenticate("admin", "correct_password") is True
    mock_set.assert_not_called()


def test_rotation_failure_does_not_block_auth(monkeypatch):
    """If set_password raises (DB hiccup, write failure), auth STILL
    succeeds because the verify_password check already passed. The
    rotation is best-effort — denying a session because the rotation
    failed would be the worst of both worlds (operator can't log in
    AND the hash still uses weak params)."""
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
        patch("genlab_core.auth.users.get_user_password_hash", return_value="$argon2id$weak..."),
        patch("genlab_core.auth.passwords.verify_password", return_value=True),
        patch("genlab_core.auth.users.update_last_login"),
        patch("genlab_core.auth.passwords.needs_rehash", return_value=True),
        # set_password raises — must not propagate
        patch("genlab_core.auth.users.set_password", side_effect=RuntimeError("DB hiccup")),
    ):
        assert mod._authenticate("admin", "correct_password") is True


def test_rotation_not_attempted_on_env_var_fallback(monkeypatch):
    """Step 2 (DB row + NULL hash) and Step 3 (no DB row) both fall
    back to env-var. Pin that needs_rehash + set_password are NOT
    called in those paths — there's no DB hash to rotate."""
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

    # Step 2: DB row exists but NULL hash
    with (
        patch("genlab_core.auth.users.get_user_by_username", return_value=user),
        patch("genlab_core.auth.users.get_user_password_hash", return_value=None),
        patch("genlab_core.auth.passwords.needs_rehash") as mock_needs,
        patch("genlab_core.auth.users.set_password") as mock_set,
    ):
        assert mod._authenticate("admin", "env_pass") is True
    mock_needs.assert_not_called()
    mock_set.assert_not_called()

    # Step 3: no DB row
    with (
        patch("genlab_core.auth.users.get_user_by_username", return_value=None),
        patch("genlab_core.auth.passwords.needs_rehash") as mock_needs,
        patch("genlab_core.auth.users.set_password") as mock_set,
    ):
        assert mod._authenticate("admin", "env_pass") is True
    mock_needs.assert_not_called()
    mock_set.assert_not_called()


def test_source_pin_rotation_helper_present():
    """The _maybe_rotate_password_hash helper exists with the
    expected name + PR anchor. Pin so a future refactor that
    inlines it surfaces in tests."""
    import server.review_server as mod

    src = Path(mod.__file__).read_text()
    assert (
        "def _maybe_rotate_password_hash(user_id, submitted_pass: str, current_hash: str) -> None:"
        in src
    )
    assert "PR #563" in src


def test_env_var_auth_match_constant_time_compare(monkeypatch):
    """The extracted env-var helper uses hmac.compare_digest. Pin
    the function exists + behaves correctly so the form-POST and
    basic-auth paths can share it."""
    import server.review_server as mod

    _patch_env_creds(monkeypatch, user="admin", password="secret")
    assert mod._env_var_auth_match("admin", "secret") is True
    assert mod._env_var_auth_match("admin", "wrong") is False
    assert mod._env_var_auth_match("wrong_user", "secret") is False
    assert mod._env_var_auth_match("", "") is False
