"""PR #559 (2026-06-25) — users table foundation read helpers.

These tests pin the public surface of ``genlab_core.auth.users``:

  * get_user_by_username(username) — auth-path lookup
  * list_users_for_tenant(slug_or_uuid) — admin UI lookup
  * User DTO + VALID_ROLES + VALID_STATUSES constants

Plus the fail-close contract: every helper returns None / [] when
DATABASE_URL is missing, when the connection raises, or when the
query returns no rows. Same conservative None / [] choice as
PR #557's tenant helpers — running against an old DB before the
migration applies doesn't crash the caller.

Tests use unittest.mock to stub the psycopg-backed connection
(no live DB needed in CI). A separate live-DB integration test
will be added in a follow-up after auth-wire ships.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

# ── module-level structure pins ────────────────────────────────────


def test_module_exports_public_surface():
    """The documented public helpers + User DTO + role/status
    constants are importable from genlab_core.auth.users."""
    from genlab_core.auth.users import (
        VALID_ROLES,
        VALID_STATUSES,
        User,
        get_user_by_username,
        list_users_for_tenant,
    )

    assert callable(get_user_by_username)
    assert callable(list_users_for_tenant)
    assert User is not None
    assert isinstance(VALID_ROLES, frozenset)
    assert isinstance(VALID_STATUSES, frozenset)


def test_valid_roles_match_documented_set():
    """The 3 roles documented in the migration + module are pinned
    here. A future role addition (e.g. 'support') must update both
    the constant + this pin."""
    from genlab_core.auth.users import VALID_ROLES

    assert VALID_ROLES == frozenset({"admin", "operator", "viewer"})


def test_valid_statuses_match_documented_set():
    from genlab_core.auth.users import VALID_STATUSES

    assert VALID_STATUSES == frozenset({"active", "suspended", "deactivated"})


def test_user_dataclass_is_frozen():
    """User is a frozen dataclass — mutations raise to prevent
    auth-path callers from accidentally rewriting identity
    mid-session. Same rationale as PR #557's Tenant DTO."""
    import dataclasses

    from genlab_core.auth.users import User

    u = User(
        id=uuid.uuid4(),
        username="x",
        display_name="X",
        tenant_id=uuid.uuid4(),
        role="operator",
        status="active",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        u.username = "y"  # type: ignore[misc]


def test_user_dto_omits_password_hash():
    """The User DTO must NOT carry password_hash — auth-path code
    that logs/serialises a User must never accidentally leak the
    hash. Verified by checking the dataclass fields."""
    import dataclasses

    from genlab_core.auth.users import User

    field_names = {f.name for f in dataclasses.fields(User)}
    assert "password_hash" not in field_names
    # Lifecycle timestamps also intentionally omitted
    assert "created_at" not in field_names
    assert "updated_at" not in field_names
    assert "last_login_at" not in field_names


# ── fail-close behavior ────────────────────────────────────────────


def test_get_user_no_dsn_returns_none(monkeypatch):
    from genlab_core.auth import users

    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert users.get_user_by_username("admin") is None


def test_list_users_no_dsn_returns_empty(monkeypatch):
    from genlab_core.auth import users

    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert users.list_users_for_tenant("self") == []


def test_empty_username_returns_none_without_db_query():
    """Defensive short-circuit: empty username → None, no DB roundtrip."""
    from genlab_core.auth import users

    with patch.object(users, "_connect") as connect_mock:
        assert users.get_user_by_username("") is None
    connect_mock.assert_not_called()


def test_empty_tenant_returns_empty_without_db_query():
    from genlab_core.auth import users

    with patch.object(users, "_connect") as connect_mock:
        assert users.list_users_for_tenant("") == []
    connect_mock.assert_not_called()


def test_db_exception_returns_fail_close_value(monkeypatch):
    """A psycopg raise inside the context manager → None / []. The
    helpers MUST NEVER propagate connection exceptions to the
    auth-path caller (auth would fail open)."""
    from genlab_core.auth import users

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_conn.execute.side_effect = RuntimeError("simulated DB blowup")

    with patch.object(users, "_connect", return_value=fake_conn):
        assert users.get_user_by_username("admin") is None
        assert users.list_users_for_tenant("self") == []


# ── happy-path behavior ────────────────────────────────────────────


def _make_conn_with_row(row):
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_cursor = MagicMock()
    fake_cursor.fetchone.return_value = row
    fake_conn.execute.return_value = fake_cursor
    return fake_conn


def _make_conn_with_rows(rows):
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_cursor = MagicMock()
    fake_cursor.fetchall.return_value = rows
    fake_conn.execute.return_value = fake_cursor
    return fake_conn


def test_get_user_returns_dto(monkeypatch):
    from genlab_core.auth import users

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    uid = uuid.uuid4()
    tid = uuid.uuid4()
    row = {
        "id": uid,
        "username": "admin",
        "display_name": "Self (admin)",
        "tenant_id": tid,
        "role": "admin",
        "status": "active",
    }
    fake_conn = _make_conn_with_row(row)
    with patch.object(users, "_connect", return_value=fake_conn):
        u = users.get_user_by_username("admin")
    assert u is not None
    assert u.id == uid
    assert u.username == "admin"
    assert u.display_name == "Self (admin)"
    assert u.tenant_id == tid
    assert u.role == "admin"
    assert u.status == "active"


def test_get_user_returns_none_when_no_row(monkeypatch):
    from genlab_core.auth import users

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = _make_conn_with_row(None)
    with patch.object(users, "_connect", return_value=fake_conn):
        assert users.get_user_by_username("nonexistent_op") is None


def test_get_user_query_is_case_sensitive_and_limit_1(monkeypatch):
    """Pin the SQL contract: WHERE username = %s (no LOWER), LIMIT 1.
    A future refactor that adds case-insensitivity must be deliberate
    (not accidental via LOWER on a non-functional-index column)."""
    from genlab_core.auth import users

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = _make_conn_with_row(None)
    with patch.object(users, "_connect", return_value=fake_conn):
        users.get_user_by_username("admin")
    sql = str(fake_conn.execute.call_args.args[0])
    assert "WHERE username = %s" in sql
    assert "LIMIT 1" in sql
    assert "LOWER(" not in sql  # case-sensitive contract


def test_list_users_by_slug_returns_sorted_by_created_at(monkeypatch):
    from genlab_core.auth import users

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    rows = [
        {
            "id": uuid.uuid4(),
            "username": "admin",
            "display_name": "Admin",
            "tenant_id": uuid.uuid4(),
            "role": "admin",
            "status": "active",
        },
        {
            "id": uuid.uuid4(),
            "username": "operator1",
            "display_name": "Op 1",
            "tenant_id": uuid.uuid4(),
            "role": "operator",
            "status": "active",
        },
    ]
    fake_conn = _make_conn_with_rows(rows)
    with patch.object(users, "_connect", return_value=fake_conn):
        out = users.list_users_for_tenant("self")
    assert len(out) == 2
    assert out[0].username == "admin"
    assert out[1].username == "operator1"
    # Pin ORDER BY created_at ASC (stable pagination)
    sql = str(fake_conn.execute.call_args.args[0])
    assert "ORDER BY u.created_at ASC" in sql


def test_list_users_by_uuid_uses_uuid_branch(monkeypatch):
    """UUID input → PK-indexed query (tenant_id = %s), no slug JOIN."""
    from genlab_core.auth import users

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = _make_conn_with_rows([])
    tid = uuid.uuid4()
    with patch.object(users, "_connect", return_value=fake_conn):
        users.list_users_for_tenant(tid)
    sql = str(fake_conn.execute.call_args.args[0])
    assert "WHERE tenant_id = %s" in sql
    assert "JOIN tenants" not in sql


def test_list_users_by_uuid_shaped_string_uses_uuid_branch(monkeypatch):
    """UUID-shaped string ALSO takes the UUID branch (no slug JOIN)."""
    from genlab_core.auth import users

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = _make_conn_with_rows([])
    with patch.object(users, "_connect", return_value=fake_conn):
        users.list_users_for_tenant(str(uuid.uuid4()))
    sql = str(fake_conn.execute.call_args.args[0])
    assert "WHERE tenant_id = %s" in sql
    assert "JOIN tenants" not in sql


def test_list_users_non_uuid_string_takes_slug_branch(monkeypatch):
    from genlab_core.auth import users

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = _make_conn_with_rows([])
    with patch.object(users, "_connect", return_value=fake_conn):
        users.list_users_for_tenant("self")
    sql = str(fake_conn.execute.call_args.args[0])
    assert "JOIN tenants t ON t.id = u.tenant_id" in sql
    assert "WHERE t.slug = %s" in sql


def test_list_users_returns_all_statuses(monkeypatch):
    """Admin UI filtering is the caller's job — the helper returns
    suspended + deactivated rows too. Verifies via the SQL: no
    WHERE status = 'active' filter."""
    from genlab_core.auth import users

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = _make_conn_with_rows([])
    with patch.object(users, "_connect", return_value=fake_conn):
        users.list_users_for_tenant("self")
    sql = str(fake_conn.execute.call_args.args[0])
    assert "status =" not in sql  # no status filter


# ── migration shape pins ───────────────────────────────────────────


def test_migration_file_exists_and_includes_admin_seed():
    """Pin the migration's seed contract — the admin user bound to
    the 'self' tenant. The username defaults to what
    REVIEW_AUTH_USER's default is so the next auth-wire PR can
    match env-var sessions to this row."""
    from pathlib import Path

    mig_path = (
        Path(__file__).resolve().parent.parent.parent
        / "migrations"
        / "versions"
        / "d0e1f2g3h4i5_users_table_foundation.py"
    )
    assert mig_path.exists(), f"missing migration file at {mig_path}"
    src = mig_path.read_text()
    # Seed: admin user bound to self tenant
    assert "'admin'" in src
    assert "'Self (admin)'" in src
    assert "WHERE t.slug = 'self'" in src
    # Idempotent contract
    assert "ON CONFLICT (username) DO NOTHING" in src
    # Cascade contract — deleting a tenant unbinds its users
    assert "ON DELETE CASCADE" in src
    # password_hash is nullable for env-var-auth users (today's state)
    assert "password_hash TEXT," in src or "password_hash TEXT\n" in src
    # role default is 'operator' (admin is the explicit seed exception)
    assert "DEFAULT 'operator'" in src
