"""Auto-resume sweeper for niche_pauses — PR after #579.

Pins:

  * sweep_expired_pauses returns -1 when DB is unavailable
    (fail-CLOSED sentinel so the CLI / systemd timer can exit
    non-zero and operators see the failure)
  * sweep_expired_pauses returns -1 when SQL execution raises
  * sweep_expired_pauses returns the cursor.rowcount on success
  * sweep_expired_pauses returns 0 (not None) when psycopg sets
    rowcount=None (edge-case defense)
  * sweep_expired_pauses INFO-logs when a non-zero count was
    deleted; DEBUG-logs the no-op case (avoid daily INFO spam)
  * SQL pin: the WHERE clause must guard with `paused_until IS
    NOT NULL` so indefinite pauses are NEVER swept
  * CLI main() returns 0 when sweep succeeds (count >= 0)
  * CLI main() returns 1 when sweep returns the -1 sentinel
  * CLI main() ignores argv (reserved for future flags) without
    crashing
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch


def _fake_conn(rowcount: int | None) -> MagicMock:
    """Build a MagicMock that mimics psycopg's context-manager +
    execute().rowcount API the library uses."""
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_cursor = MagicMock()
    fake_cursor.rowcount = rowcount
    fake_conn.execute.return_value = fake_cursor
    return fake_conn


# ── sweep_expired_pauses ───────────────────────────────────────────


def test_sweep_returns_neg1_when_no_dsn(monkeypatch):
    """Fail-CLOSED sentinel — caller (CLI / timer) exits non-zero."""
    from genlab_core.scheduling import niche_pause

    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert niche_pause.sweep_expired_pauses() == -1


def test_sweep_returns_neg1_when_sql_raises(monkeypatch, caplog):
    from genlab_core.scheduling import niche_pause

    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_conn.execute.side_effect = RuntimeError("simulated SQL error")

    with patch.object(niche_pause, "_connect", return_value=fake_conn):
        with caplog.at_level(logging.WARNING):
            result = niche_pause.sweep_expired_pauses()

    assert result == -1
    assert any("sweep_expired_pauses failed" in r.getMessage() for r in caplog.records)


def test_sweep_returns_rowcount_on_success(monkeypatch, caplog):
    from genlab_core.scheduling import niche_pause

    with patch.object(niche_pause, "_connect", return_value=_fake_conn(rowcount=3)):
        with caplog.at_level(logging.INFO):
            result = niche_pause.sweep_expired_pauses()

    assert result == 3
    assert any("deleted 3 row(s)" in r.getMessage() for r in caplog.records)


def test_sweep_returns_zero_when_nothing_to_delete(monkeypatch, caplog):
    """No-op case must NOT log at INFO — operators don't want
    a daily journalctl line saying 'deleted 0 row(s)'."""
    from genlab_core.scheduling import niche_pause

    with patch.object(niche_pause, "_connect", return_value=_fake_conn(rowcount=0)):
        with caplog.at_level(logging.DEBUG):
            result = niche_pause.sweep_expired_pauses()

    assert result == 0
    # INFO-level "deleted" line must NOT appear for the no-op case
    info_records = [r for r in caplog.records if r.levelno >= logging.INFO]
    assert not any("deleted" in r.getMessage() for r in info_records)


def test_sweep_handles_rowcount_none_edge_case(monkeypatch):
    """psycopg can return None on rowcount in some driver
    configurations. Defensive coercion → 0, not a TypeError."""
    from genlab_core.scheduling import niche_pause

    with patch.object(niche_pause, "_connect", return_value=_fake_conn(rowcount=None)):
        result = niche_pause.sweep_expired_pauses()

    assert result == 0


def test_sweep_sql_guards_indefinite_pauses(monkeypatch):
    """SAFETY PIN: the WHERE clause MUST include `paused_until IS
    NOT NULL` so indefinite pauses (NULL) are NEVER swept.
    Dropping this guard would silently re-enable publishing on a
    niche the operator deliberately halted indefinitely."""
    from genlab_core.scheduling import niche_pause

    fake_conn = _fake_conn(rowcount=0)
    with patch.object(niche_pause, "_connect", return_value=fake_conn):
        niche_pause.sweep_expired_pauses()

    # First positional arg to .execute() is the SQL string
    call_args = fake_conn.execute.call_args
    sql = call_args.args[0] if call_args.args else call_args.kwargs.get("query", "")
    # Normalize whitespace so formatter rewraps don't break the pin
    import re

    sql_normalized = re.sub(r"\s+", " ", sql).strip()
    assert "paused_until IS NOT NULL" in sql_normalized, (
        "WHERE clause must guard NULL — indefinite pauses must NEVER be swept"
    )
    assert "paused_until < NOW()" in sql_normalized
    # Must NOT use just `< NOW()` without the IS NOT NULL guard
    # (Postgres treats `NULL < NOW()` as NULL = filtered out, so
    # this is belt+suspenders — but the explicit guard makes the
    # intent obvious to the next reader.)


# ── CLI main() ────────────────────────────────────────────────────


def test_cli_main_returns_zero_on_success():
    from genlab_core.scheduling.niche_pause_sweeper import main

    with patch(
        "genlab_core.scheduling.niche_pause.sweep_expired_pauses",
        return_value=5,
    ):
        assert main([]) == 0


def test_cli_main_returns_zero_on_no_op_success():
    """Count==0 is still a successful sweep — must NOT exit 1."""
    from genlab_core.scheduling.niche_pause_sweeper import main

    with patch(
        "genlab_core.scheduling.niche_pause.sweep_expired_pauses",
        return_value=0,
    ):
        assert main([]) == 0


def test_cli_main_returns_one_on_sentinel():
    """sweep returning -1 → CLI exits 1 → systemd surfaces failure."""
    from genlab_core.scheduling.niche_pause_sweeper import main

    with patch(
        "genlab_core.scheduling.niche_pause.sweep_expired_pauses",
        return_value=-1,
    ):
        assert main([]) == 1


def test_cli_main_accepts_argv_without_crashing():
    """argv is reserved for future flags — ignored today, but the
    signature must accept it so future --dry-run / --quiet doesn't
    break the systemd unit's command line."""
    from genlab_core.scheduling.niche_pause_sweeper import main

    with patch(
        "genlab_core.scheduling.niche_pause.sweep_expired_pauses",
        return_value=0,
    ):
        # Passing extra argv must not raise
        assert main(["--future-flag", "value"]) == 0


# ── source pin ────────────────────────────────────────────────────


def test_source_pin_cli_dispatches_to_library():
    """The CLI module MUST import + delegate to the library
    function — a future refactor that inlines the SQL into the
    CLI would lose the library's reusability for the dashboard /
    programmatic-trigger surfaces. Pin so that drift surfaces."""
    import genlab_core.scheduling.niche_pause_sweeper as mod

    src = Path(mod.__file__).read_text()
    # CLI calls the library function (no inline DELETE)
    assert "from genlab_core.scheduling.niche_pause import sweep_expired_pauses" in src
    assert "sweep_expired_pauses()" in src
    # No inline SQL in the CLI module
    assert "DELETE FROM niche_pauses" not in src
