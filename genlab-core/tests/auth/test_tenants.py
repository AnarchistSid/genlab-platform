"""PR #557 (2026-06-25) — tenant model foundation read helpers.

These tests pin the public surface of ``genlab_core.auth.tenants``:

  * get_tenant_for_niche(niche_id) — reverse lookup
  * list_niches_for_tenant(slug_or_uuid) — forward lookup
  * get_tenant_by_slug(slug) — auth-path lookup

Plus the fail-close contract: every helper returns None / [] when
DATABASE_URL is missing, when the connection raises, or when the
query returns no rows. The conservative None / [] choice means
running against an old DB before the migration applies doesn't
crash the caller — it degrades to "unknown tenant" and the caller
falls back to legacy niche-only behavior.

Tests use unittest.mock to stub the psycopg-backed connection
(no live DB needed in CI). A separate live-DB test will be added
in a follow-up PR after the migration is live + the seed runs on
a test DB.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

# ── module-level structure pins ────────────────────────────────────


def test_module_exports_public_surface():
    """The three documented public helpers + the Tenant DTO are
    importable from genlab_core.auth.tenants."""
    from genlab_core.auth.tenants import (
        Tenant,
        get_tenant_by_slug,
        get_tenant_for_niche,
        list_niches_for_tenant,
    )

    assert callable(get_tenant_for_niche)
    assert callable(list_niches_for_tenant)
    assert callable(get_tenant_by_slug)
    assert Tenant is not None


def test_tenant_dataclass_is_frozen():
    """Tenant is a frozen dataclass — mutations raise to prevent
    auth-path callers from accidentally rewriting tenant identity
    mid-session."""
    import dataclasses

    from genlab_core.auth.tenants import Tenant

    t = Tenant(id=uuid.uuid4(), slug="x", name="X", status="active")
    with pytest.raises(dataclasses.FrozenInstanceError):
        t.slug = "y"  # type: ignore[misc]


# ── fail-close behavior ────────────────────────────────────────────


def test_get_tenant_for_niche_no_dsn_returns_none(monkeypatch):
    """No DATABASE_URL → None. Caller falls back to legacy niche-only."""
    from genlab_core.auth import tenants

    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert tenants.get_tenant_for_niche("gaming") is None


def test_list_niches_no_dsn_returns_empty(monkeypatch):
    from genlab_core.auth import tenants

    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert tenants.list_niches_for_tenant("self") == []


def test_get_by_slug_no_dsn_returns_none(monkeypatch):
    from genlab_core.auth import tenants

    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert tenants.get_tenant_by_slug("self") is None


def test_empty_niche_id_returns_none_without_db_query(monkeypatch):
    """Defensive: empty niche_id short-circuits before the DB
    roundtrip. Pin via the connection helper getting NO calls."""
    from genlab_core.auth import tenants

    with patch.object(tenants, "_connect") as connect_mock:
        assert tenants.get_tenant_for_niche("") is None
    connect_mock.assert_not_called()


def test_empty_tenant_slug_returns_none_without_db_query(monkeypatch):
    from genlab_core.auth import tenants

    with patch.object(tenants, "_connect") as connect_mock:
        assert tenants.get_tenant_by_slug("") is None
        assert tenants.list_niches_for_tenant("") == []
    connect_mock.assert_not_called()


def test_db_exception_returns_fail_close_value(monkeypatch):
    """A psycopg raise inside the context manager → None / []. The
    helpers must NEVER propagate connection exceptions to the
    auth-path caller (auth would fail open). Pin both shapes."""
    from genlab_core.auth import tenants

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")

    # Build a context manager whose .execute raises on call
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_conn.execute.side_effect = RuntimeError("simulated DB blowup")

    with patch.object(tenants, "_connect", return_value=fake_conn):
        assert tenants.get_tenant_for_niche("gaming") is None
        assert tenants.get_tenant_by_slug("self") is None
        assert tenants.list_niches_for_tenant("self") == []


# ── happy-path behavior ────────────────────────────────────────────


def _make_conn_with_row(row):
    """Build a fake conn whose .execute().fetchone() returns row."""
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_cursor = MagicMock()
    fake_cursor.fetchone.return_value = row
    fake_conn.execute.return_value = fake_cursor
    return fake_conn


def _make_conn_with_rows(rows):
    """Build a fake conn whose .execute().fetchall() returns rows."""
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_cursor = MagicMock()
    fake_cursor.fetchall.return_value = rows
    fake_conn.execute.return_value = fake_cursor
    return fake_conn


def test_get_tenant_for_niche_returns_dto(monkeypatch):
    from genlab_core.auth import tenants

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    tid = uuid.uuid4()
    row = {"id": tid, "slug": "self", "name": "Self (owner)", "status": "active"}
    fake_conn = _make_conn_with_row(row)
    with patch.object(tenants, "_connect", return_value=fake_conn):
        t = tenants.get_tenant_for_niche("gaming")
    assert t is not None
    assert t.id == tid
    assert t.slug == "self"
    assert t.name == "Self (owner)"
    assert t.status == "active"


def test_get_tenant_for_niche_returns_none_when_no_row(monkeypatch):
    from genlab_core.auth import tenants

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = _make_conn_with_row(None)
    with patch.object(tenants, "_connect", return_value=fake_conn):
        assert tenants.get_tenant_for_niche("orphan_niche") is None


def test_get_tenant_for_niche_picks_oldest_for_co_owned(monkeypatch):
    """If a niche is co-owned (rare; the join table allows it), the
    helper returns the OLDEST tenant binding. Pin the ORDER BY +
    LIMIT 1 contract by inspecting the SQL the cursor sees."""
    from genlab_core.auth import tenants

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = _make_conn_with_row(
        {"id": uuid.uuid4(), "slug": "self", "name": "Self", "status": "active"}
    )
    with patch.object(tenants, "_connect", return_value=fake_conn):
        tenants.get_tenant_for_niche("gaming")
    sql = str(fake_conn.execute.call_args.args[0])
    assert "ORDER BY t.created_at ASC" in sql
    assert "LIMIT 1" in sql


def test_list_niches_by_slug_returns_sorted_list(monkeypatch):
    from genlab_core.auth import tenants

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    # The SQL has ORDER BY tn.niche_id — the test fixture returns
    # already-sorted rows; pin that the helper passes them through.
    rows = [{"niche_id": n} for n in ["ai_creators", "anime", "gaming", "movies", "sports"]]
    fake_conn = _make_conn_with_rows(rows)
    with patch.object(tenants, "_connect", return_value=fake_conn):
        out = tenants.list_niches_for_tenant("self")
    assert out == ["ai_creators", "anime", "gaming", "movies", "sports"]


def test_list_niches_by_uuid_uses_uuid_branch(monkeypatch):
    """When passed a UUID object, the helper takes the PK-indexed
    query branch (tenant_id = %s) instead of the slug join. Pin via
    the SQL shape."""
    from genlab_core.auth import tenants

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = _make_conn_with_rows([])
    tid = uuid.uuid4()
    with patch.object(tenants, "_connect", return_value=fake_conn):
        tenants.list_niches_for_tenant(tid)
    sql = str(fake_conn.execute.call_args.args[0])
    assert "tenant_id = %s" in sql
    assert "JOIN tenants" not in sql  # slug-join branch NOT taken


def test_list_niches_uuid_shaped_string_uses_uuid_branch(monkeypatch):
    """Passing a string that PARSES as a UUID also takes the
    UUID branch (slug strings like 'self' do not parse). Pin both
    branches via SQL shape."""
    from genlab_core.auth import tenants

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = _make_conn_with_rows([])
    with patch.object(tenants, "_connect", return_value=fake_conn):
        tenants.list_niches_for_tenant(str(uuid.uuid4()))  # uuid-shaped string
    sql = str(fake_conn.execute.call_args.args[0])
    assert "tenant_id = %s" in sql
    assert "JOIN tenants" not in sql


def test_list_niches_non_uuid_string_takes_slug_branch(monkeypatch):
    from genlab_core.auth import tenants

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = _make_conn_with_rows([])
    with patch.object(tenants, "_connect", return_value=fake_conn):
        tenants.list_niches_for_tenant("self")  # not UUID-shaped
    sql = str(fake_conn.execute.call_args.args[0])
    assert "JOIN tenants t ON t.id = tn.tenant_id" in sql
    assert "WHERE t.slug = %s" in sql


def test_get_tenant_by_slug_returns_dto(monkeypatch):
    from genlab_core.auth import tenants

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    tid = uuid.uuid4()
    fake_conn = _make_conn_with_row(
        {"id": tid, "slug": "acme", "name": "Acme Brands", "status": "active"}
    )
    with patch.object(tenants, "_connect", return_value=fake_conn):
        t = tenants.get_tenant_by_slug("acme")
    assert t is not None
    assert t.slug == "acme"
    assert t.name == "Acme Brands"


# ── migration shape pins ───────────────────────────────────────────


def test_migration_file_exists_and_includes_self_seed():
    """Pin the migration's seed contract — the 'self' tenant + 5
    niche bindings are part of the upgrade path. Future migrations
    must not break this."""
    from pathlib import Path

    mig_path = (
        Path(__file__).resolve().parent.parent.parent
        / "migrations"
        / "versions"
        / "c9d0e1f2g3h4_tenant_model_foundation.py"
    )
    assert mig_path.exists(), f"missing migration file at {mig_path}"
    src = mig_path.read_text()
    # The seed contract — self tenant + 5 known niche bindings
    assert "'self'" in src
    for niche in ("ai_creators", "gaming", "sports", "movies", "anime"):
        assert f"'{niche}'" in src
    # Idempotent contract — ON CONFLICT DO NOTHING on both inserts
    assert "ON CONFLICT (slug) DO NOTHING" in src
    assert "ON CONFLICT (tenant_id, niche_id) DO NOTHING" in src
    # Cascade contract — deleting a tenant unbinds its niches
    assert "ON DELETE CASCADE" in src
