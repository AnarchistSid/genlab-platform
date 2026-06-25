"""PR #577 (2026-06-25) — per-niche pause primitive.

Pins:

  * NichePause frozen dataclass
  * is_paused: True/False contract + fail-OPEN on errors
  * get_pause: NichePause | None contract
  * list_active_pauses: DESC by created_at, empty on error
  * pause: idempotent upsert; fail-CLOSED on errors
  * unpause: idempotent delete; fail-CLOSED on errors
  * Auto-expiry semantics: paused_until in past = treated as unpaused
  * Reason required (server-side enforcement)
  * Migration shape pin: table + index + RLS
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

# ── NichePause dataclass ───────────────────────────────────────────


def test_pause_dataclass_frozen():
    """Same auth-path safety pattern as Tenant / User / ComplianceDecision."""
    from genlab_core.scheduling.niche_pause import NichePause

    p = NichePause(
        niche_id="gaming",
        paused_until=None,
        reason="copyright_strike",
        paused_by="admin",
        created_at=datetime(2026, 6, 25, tzinfo=UTC),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.reason = "edited"  # type: ignore[misc]


# ── is_paused ──────────────────────────────────────────────────────


def test_is_paused_no_dsn_returns_false(monkeypatch):
    """Fail-OPEN: missing DSN → False, not raise. Auto-approver
    proceeds under 'no pause known'."""
    from genlab_core.scheduling import niche_pause

    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert niche_pause.is_paused("gaming") is False


def test_is_paused_empty_niche_id_returns_false():
    from genlab_core.scheduling.niche_pause import is_paused

    assert is_paused("") is False


def test_is_paused_true_when_row_exists(monkeypatch):
    from genlab_core.scheduling import niche_pause

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_cursor = MagicMock()
    fake_cursor.fetchone.return_value = {"paused_until": None}
    fake_conn.execute.return_value = fake_cursor

    with patch.object(niche_pause, "_connect", return_value=fake_conn):
        assert niche_pause.is_paused("gaming") is True


def test_is_paused_false_when_no_row(monkeypatch):
    from genlab_core.scheduling import niche_pause

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_cursor = MagicMock()
    fake_cursor.fetchone.return_value = None
    fake_conn.execute.return_value = fake_cursor

    with patch.object(niche_pause, "_connect", return_value=fake_conn):
        assert niche_pause.is_paused("gaming") is False


def test_is_paused_sql_filters_expired_pauses(monkeypatch):
    """Pin the SQL contract: WHERE paused_until IS NULL OR paused_until > NOW().
    Expired auto-pause rows are treated as unpaused without needing
    a sweeper job — the read query filters them out."""
    from genlab_core.scheduling import niche_pause

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_cursor = MagicMock()
    fake_cursor.fetchone.return_value = None
    fake_conn.execute.return_value = fake_cursor

    with patch.object(niche_pause, "_connect", return_value=fake_conn):
        niche_pause.is_paused("gaming")
    sql = str(fake_conn.execute.call_args.args[0])
    assert "paused_until IS NULL OR paused_until > NOW()" in sql


def test_is_paused_db_exception_returns_false_fail_open(monkeypatch):
    """Fail-OPEN: DB exception → False (not True, not raise).
    Critical: auto-approver MUST NOT halt on a transient DB hiccup."""
    from genlab_core.scheduling import niche_pause

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_conn.execute.side_effect = RuntimeError("simulated DB error")

    with patch.object(niche_pause, "_connect", return_value=fake_conn):
        assert niche_pause.is_paused("gaming") is False


# ── get_pause ──────────────────────────────────────────────────────


def test_get_pause_returns_dto_when_row_exists(monkeypatch):
    from genlab_core.scheduling import niche_pause

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    until = datetime(2026, 6, 26, tzinfo=UTC)
    created = datetime(2026, 6, 25, tzinfo=UTC)
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_cursor = MagicMock()
    fake_cursor.fetchone.return_value = {
        "niche_id": "gaming",
        "paused_until": until,
        "reason": "copyright_strike_received",
        "paused_by": "admin",
        "created_at": created,
    }
    fake_conn.execute.return_value = fake_cursor

    with patch.object(niche_pause, "_connect", return_value=fake_conn):
        p = niche_pause.get_pause("gaming")
    assert p is not None
    assert p.niche_id == "gaming"
    assert p.paused_until == until
    assert p.reason == "copyright_strike_received"
    assert p.paused_by == "admin"
    assert p.created_at == created


def test_get_pause_returns_none_on_no_row(monkeypatch):
    from genlab_core.scheduling import niche_pause

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_cursor = MagicMock()
    fake_cursor.fetchone.return_value = None
    fake_conn.execute.return_value = fake_cursor

    with patch.object(niche_pause, "_connect", return_value=fake_conn):
        assert niche_pause.get_pause("gaming") is None


# ── list_active_pauses ─────────────────────────────────────────────


def test_list_active_returns_dtos(monkeypatch):
    from genlab_core.scheduling import niche_pause

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_cursor = MagicMock()
    fake_cursor.fetchall.return_value = [
        {
            "niche_id": "gaming",
            "paused_until": None,
            "reason": "investigating",
            "paused_by": "admin",
            "created_at": datetime(2026, 6, 25, 12, 0, tzinfo=UTC),
        },
        {
            "niche_id": "anime",
            "paused_until": datetime(2026, 6, 26, tzinfo=UTC),
            "reason": "shadowban_warning",
            "paused_by": None,
            "created_at": datetime(2026, 6, 25, 10, 0, tzinfo=UTC),
        },
    ]
    fake_conn.execute.return_value = fake_cursor

    with patch.object(niche_pause, "_connect", return_value=fake_conn):
        pauses = niche_pause.list_active_pauses()
    assert len(pauses) == 2
    assert pauses[0].niche_id == "gaming"
    assert pauses[1].niche_id == "anime"


def test_list_active_sql_orders_by_created_at_desc(monkeypatch):
    """Pin DESC ordering — newest pauses first (most operator-
    relevant for the dashboard card)."""
    from genlab_core.scheduling import niche_pause

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_cursor = MagicMock()
    fake_cursor.fetchall.return_value = []
    fake_conn.execute.return_value = fake_cursor

    with patch.object(niche_pause, "_connect", return_value=fake_conn):
        niche_pause.list_active_pauses()
    sql = str(fake_conn.execute.call_args.args[0])
    assert "ORDER BY created_at DESC" in sql


def test_list_active_no_dsn_returns_empty(monkeypatch):
    from genlab_core.scheduling import niche_pause

    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert niche_pause.list_active_pauses() == []


# ── pause (write) ──────────────────────────────────────────────────


def test_pause_empty_niche_id_returns_false():
    from genlab_core.scheduling.niche_pause import pause

    assert pause("", "any reason") is False


def test_pause_empty_reason_returns_false():
    """Server-side enforcement: every pause MUST have a reason for
    the audit log. The dashboard form requires it; the helper
    requires it too (defense in depth — a misbehaving API client
    can't pause without a reason)."""
    from genlab_core.scheduling.niche_pause import pause

    assert pause("gaming", "") is False
    assert pause("gaming", "   ") is False  # whitespace-only


def test_pause_no_dsn_returns_false_fail_closed(monkeypatch):
    """Fail-CLOSED: write failure surfaces. Operator's pause click
    failing returns False so the UI can re-prompt; silently
    dropping a pause is the worst-case outcome."""
    from genlab_core.scheduling.niche_pause import pause

    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert pause("gaming", "copyright_strike") is False


def test_pause_uses_on_conflict_upsert(monkeypatch):
    """Pin SQL contract: INSERT ... ON CONFLICT (niche_id) DO UPDATE.
    Lets operator re-pause an already-paused niche with a new reason
    without first unpausing."""
    from genlab_core.scheduling import niche_pause

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn

    with patch.object(niche_pause, "_connect", return_value=fake_conn):
        result = niche_pause.pause("gaming", "copyright_strike", paused_by="admin")
    assert result is True
    sql = str(fake_conn.execute.call_args.args[0])
    assert "INSERT INTO niche_pauses" in sql
    assert "ON CONFLICT (niche_id) DO UPDATE" in sql


def test_pause_strips_reason_whitespace(monkeypatch):
    """Reason whitespace stripped before storage."""
    from genlab_core.scheduling import niche_pause

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn

    with patch.object(niche_pause, "_connect", return_value=fake_conn):
        niche_pause.pause("gaming", "  copyright_strike  ")
    bound = fake_conn.execute.call_args.args[1]
    # Positional args: (niche_id, paused_until, reason, paused_by)
    assert bound[2] == "copyright_strike"


def test_pause_with_auto_expiry(monkeypatch):
    """Operator passes paused_until → bound to the SQL params."""
    from genlab_core.scheduling import niche_pause

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn

    until = datetime(2026, 6, 26, tzinfo=UTC)
    with patch.object(niche_pause, "_connect", return_value=fake_conn):
        niche_pause.pause("gaming", "investigating", paused_until=until)
    bound = fake_conn.execute.call_args.args[1]
    assert bound[1] == until


def test_pause_db_exception_returns_false(monkeypatch):
    """DB exception → False (fail-CLOSED). Operator's UI sees the
    failure and can retry."""
    from genlab_core.scheduling import niche_pause

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_conn.execute.side_effect = RuntimeError("simulated DB error")

    with patch.object(niche_pause, "_connect", return_value=fake_conn):
        assert niche_pause.pause("gaming", "copyright_strike") is False


# ── unpause ────────────────────────────────────────────────────────


def test_unpause_empty_returns_false():
    from genlab_core.scheduling.niche_pause import unpause

    assert unpause("") is False


def test_unpause_no_dsn_returns_false(monkeypatch):
    from genlab_core.scheduling.niche_pause import unpause

    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert unpause("gaming") is False


def test_unpause_executes_delete(monkeypatch):
    from genlab_core.scheduling import niche_pause

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn

    with patch.object(niche_pause, "_connect", return_value=fake_conn):
        result = niche_pause.unpause("gaming")
    assert result is True
    sql = str(fake_conn.execute.call_args.args[0])
    assert "DELETE FROM niche_pauses" in sql


def test_unpause_idempotent_when_no_row(monkeypatch):
    """Operator clicking 'resume' on an already-resumed niche shouldn't
    error. DELETE WHERE niche_id=X with no matching row is a no-op
    that returns True (the row is gone, which is what was wanted)."""
    from genlab_core.scheduling import niche_pause

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn

    with patch.object(niche_pause, "_connect", return_value=fake_conn):
        assert niche_pause.unpause("not_paused_niche") is True


def test_unpause_db_exception_returns_false(monkeypatch):
    from genlab_core.scheduling import niche_pause

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_conn.execute.side_effect = RuntimeError("simulated")

    with patch.object(niche_pause, "_connect", return_value=fake_conn):
        assert niche_pause.unpause("gaming") is False


# ── migration shape pins ───────────────────────────────────────────


def test_migration_file_exists_with_correct_shape():
    """Pin schema + RLS + index — future schema changes can't drop
    these without surfacing in tests."""
    from pathlib import Path

    mig = (
        Path(__file__).resolve().parent.parent.parent
        / "migrations"
        / "versions"
        / "f2g3h4i5j6k7_niche_pauses_table.py"
    )
    assert mig.exists(), f"missing migration at {mig}"
    src = mig.read_text()
    # Table shape
    assert "CREATE TABLE IF NOT EXISTS niche_pauses" in src
    assert "niche_id TEXT PRIMARY KEY" in src
    assert "paused_until TIMESTAMPTZ" in src
    assert "reason TEXT NOT NULL" in src
    # Partial index for the auto-resume sweeper query
    assert "WHERE paused_until IS NOT NULL" in src
    # RLS standard 16-table convention
    assert "ENABLE ROW LEVEL SECURITY" in src
    assert "current_setting('app.niche_id'" in src
    assert "IN ('', 'all')" in src
