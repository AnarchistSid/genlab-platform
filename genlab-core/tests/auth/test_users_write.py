"""PR #561 (2026-06-25) — users write-side helpers.

Pins for create_user, set_password, update_last_login:

  * Hash-then-write order (hashing failure doesn't leave half-row)
  * Input validation raises ValueError for caller-misuse (empty
    fields, invalid role/status)
  * Fail-close return value (None / False) for runtime failures
    (DSN missing, FK violation, connection error)
  * SQL contract pins (UNIQUE conflict via ON CONFLICT DO NOTHING,
    RETURNING shape, password_hash atomic with INSERT, etc.)
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

# ── create_user — input validation ─────────────────────────────────


def test_create_user_rejects_empty_username():
    from genlab_core.auth.users import create_user

    with pytest.raises(ValueError, match="username"):
        create_user("", "Display", uuid.uuid4())


def test_create_user_rejects_empty_display_name():
    from genlab_core.auth.users import create_user

    with pytest.raises(ValueError, match="display_name"):
        create_user("admin", "", uuid.uuid4())


def test_create_user_rejects_invalid_role():
    from genlab_core.auth.users import create_user

    with pytest.raises(ValueError, match="role"):
        create_user("admin", "X", uuid.uuid4(), role="god_mode")


def test_create_user_rejects_invalid_status():
    from genlab_core.auth.users import create_user

    with pytest.raises(ValueError, match="status"):
        create_user("admin", "X", uuid.uuid4(), status="evicted")


def test_create_user_accepts_all_valid_roles():
    """Pin every documented role is accepted by the validator.
    Future role additions must update VALID_ROLES + this list."""
    from genlab_core.auth.users import VALID_ROLES, create_user

    monkeypatched_env = patch.dict("os.environ", {}, clear=False)
    # No DATABASE_URL → returns None, but the ValueError shouldn't
    # fire for any valid role
    with monkeypatched_env:
        for role in VALID_ROLES:
            # No DSN → falls through to _connect None branch → returns None,
            # NOT a ValueError. That's what we're pinning here.
            result = create_user("test_" + role, "T", uuid.uuid4(), role=role)
            assert result is None  # no DSN, not a misuse error


# ── create_user — hash-first contract ──────────────────────────────


def test_create_user_with_password_hashes_before_db_call(monkeypatch):
    """Critical contract: the password is hashed BEFORE the DB
    roundtrip. If hashing fails (e.g. argon2-cffi missing), the
    DB INSERT must NOT have been attempted (no half-created row).
    """
    from genlab_core.auth import users

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")

    # Stub hash_password to raise; assert _connect not called
    with patch("genlab_core.auth.passwords.hash_password", side_effect=RuntimeError("hash boom")):
        with patch.object(users, "_connect") as connect_mock:
            with pytest.raises(RuntimeError, match="hash boom"):
                users.create_user("admin", "X", uuid.uuid4(), password="any")
    connect_mock.assert_not_called()


def test_create_user_no_password_skips_hashing(monkeypatch):
    """The PR #559 seed pattern: password=None means env-var-auth
    user. No call to hash_password — the column stores NULL."""
    from genlab_core.auth import users

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")

    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_cursor = MagicMock()
    fake_cursor.fetchone.return_value = {
        "id": uuid.uuid4(),
        "username": "admin",
        "display_name": "X",
        "tenant_id": uuid.uuid4(),
        "role": "operator",
        "status": "active",
    }
    fake_conn.execute.return_value = fake_cursor

    with patch.object(users, "_connect", return_value=fake_conn):
        with patch("genlab_core.auth.passwords.hash_password") as hash_mock:
            result = users.create_user("admin", "X", uuid.uuid4())
    assert result is not None
    hash_mock.assert_not_called()
    # NULL password_hash bound (the 5th positional in the params)
    bound_params = fake_conn.execute.call_args.args[1]
    assert bound_params[4] is None  # password_hash slot


# ── create_user — DB failure modes ─────────────────────────────────


def test_create_user_no_dsn_returns_none(monkeypatch):
    from genlab_core.auth import users

    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert users.create_user("admin", "X", uuid.uuid4()) is None


def test_create_user_returns_none_on_unique_conflict(monkeypatch):
    """ON CONFLICT DO NOTHING fires → RETURNING returns no row →
    create_user returns None + logs WARNING."""
    from genlab_core.auth import users

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")

    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_cursor = MagicMock()
    fake_cursor.fetchone.return_value = None  # ON CONFLICT swallowed the row
    fake_conn.execute.return_value = fake_cursor

    with patch.object(users, "_connect", return_value=fake_conn):
        result = users.create_user("admin", "X", uuid.uuid4())
    assert result is None


def test_create_user_db_exception_returns_none(monkeypatch):
    from genlab_core.auth import users

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_conn.execute.side_effect = RuntimeError("FK violation")

    with patch.object(users, "_connect", return_value=fake_conn):
        result = users.create_user("admin", "X", uuid.uuid4())
    assert result is None


# ── create_user — SQL contract pins ────────────────────────────────


def test_create_user_sql_has_on_conflict_and_returning(monkeypatch):
    from genlab_core.auth import users

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_cursor = MagicMock()
    fake_cursor.fetchone.return_value = None
    fake_conn.execute.return_value = fake_cursor

    with patch.object(users, "_connect", return_value=fake_conn):
        users.create_user("admin", "X", uuid.uuid4())
    sql = str(fake_conn.execute.call_args.args[0])
    assert "INSERT INTO users" in sql
    # Idempotent contract — same pattern as PR #557's tenant seed
    assert "ON CONFLICT (username) DO NOTHING" in sql
    # Returning shape matches _row_to_user
    assert "RETURNING" in sql
    assert "id, username, display_name, tenant_id, role, status" in sql


# ── set_password ───────────────────────────────────────────────────


def test_set_password_rejects_empty_user_id():
    from genlab_core.auth.users import set_password

    assert set_password("", "any_password") is False


def test_set_password_no_dsn_returns_false(monkeypatch):
    from genlab_core.auth import users

    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert users.set_password(uuid.uuid4(), "any") is False


def test_set_password_hashes_before_update(monkeypatch):
    """Same hash-first contract as create_user — a hash failure
    must NOT trigger an UPDATE with a NULL or bogus hash."""
    from genlab_core.auth import users

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    with patch("genlab_core.auth.passwords.hash_password", side_effect=ValueError("empty")):
        with patch.object(users, "_connect") as connect_mock:
            with pytest.raises(ValueError):
                users.set_password(uuid.uuid4(), "")
    connect_mock.assert_not_called()


def test_set_password_returns_true_when_row_updated(monkeypatch):
    from genlab_core.auth import users

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_result = MagicMock()
    fake_result.rowcount = 1
    fake_conn.execute.return_value = fake_result

    with patch.object(users, "_connect", return_value=fake_conn):
        assert users.set_password(uuid.uuid4(), "newpass") is True


def test_set_password_returns_false_when_no_row(monkeypatch):
    """user_id not in table → UPDATE affects 0 rows → False."""
    from genlab_core.auth import users

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_result = MagicMock()
    fake_result.rowcount = 0
    fake_conn.execute.return_value = fake_result

    with patch.object(users, "_connect", return_value=fake_conn):
        assert users.set_password(uuid.uuid4(), "newpass") is False


def test_set_password_updates_updated_at_too(monkeypatch):
    """Pin the SQL bumps updated_at = NOW() alongside the hash so
    the lifecycle timestamp reflects the change."""
    from genlab_core.auth import users

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_result = MagicMock()
    fake_result.rowcount = 1
    fake_conn.execute.return_value = fake_result

    with patch.object(users, "_connect", return_value=fake_conn):
        users.set_password(uuid.uuid4(), "newpass")
    sql = str(fake_conn.execute.call_args.args[0])
    assert "password_hash = %s" in sql
    assert "updated_at = NOW()" in sql


# ── update_last_login ──────────────────────────────────────────────


def test_update_last_login_no_dsn_returns_false(monkeypatch):
    from genlab_core.auth import users

    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert users.update_last_login(uuid.uuid4()) is False


def test_update_last_login_returns_true_on_success(monkeypatch):
    from genlab_core.auth import users

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_result = MagicMock()
    fake_result.rowcount = 1
    fake_conn.execute.return_value = fake_result

    with patch.object(users, "_connect", return_value=fake_conn):
        assert users.update_last_login(uuid.uuid4()) is True


def test_update_last_login_best_effort_returns_false_silently(monkeypatch):
    """The contract: callers must NOT see exceptions. update_last_login
    is best-effort — auth shouldn't fail because the timestamp write
    failed. Pin that DB exceptions convert to False."""
    from genlab_core.auth import users

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_conn.execute.side_effect = RuntimeError("simulated")

    with patch.object(users, "_connect", return_value=fake_conn):
        # No raise, returns False
        assert users.update_last_login(uuid.uuid4()) is False


def test_update_last_login_sql_uses_now_function(monkeypatch):
    """Pin NOW() (server-side timestamp) rather than Python-side
    datetime — avoids clock-skew across multiple app instances."""
    from genlab_core.auth import users

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_result = MagicMock()
    fake_result.rowcount = 1
    fake_conn.execute.return_value = fake_result

    with patch.object(users, "_connect", return_value=fake_conn):
        users.update_last_login(uuid.uuid4())
    sql = str(fake_conn.execute.call_args.args[0])
    assert "last_login_at = NOW()" in sql


# ── end-to-end integration with real argon2 ────────────────────────


def test_create_user_then_verify_password_roundtrip(monkeypatch):
    """End-to-end pin: create with password → fetch hash → verify
    against the original plaintext succeeds, against a wrong one
    fails. Uses real argon2 (no hash mocking) to catch any
    integration issue between create_user + verify_password."""
    from genlab_core.auth import users
    from genlab_core.auth.passwords import verify_password

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")

    captured_params: dict = {}
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn

    def fake_execute(sql, params):
        captured_params["values"] = params
        fake_cursor = MagicMock()
        fake_cursor.fetchone.return_value = {
            "id": uuid.uuid4(),
            "username": params[0],
            "display_name": params[1],
            "tenant_id": uuid.uuid4(),
            "role": params[3],
            "status": params[5],
        }
        return fake_cursor

    fake_conn.execute.side_effect = fake_execute

    with patch.object(users, "_connect", return_value=fake_conn):
        users.create_user("alice", "Alice", uuid.uuid4(), password="s3cret!")

    # The hashed password landed in the INSERT params
    password_hash = captured_params["values"][4]
    assert password_hash is not None
    assert password_hash.startswith("$argon2id$")

    # Roundtrip verify
    assert verify_password("s3cret!", password_hash) is True
    assert verify_password("wrong", password_hash) is False
