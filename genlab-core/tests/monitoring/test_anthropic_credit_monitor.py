"""Tests for ``monitoring.anthropic_credit_monitor`` (PR for 2026-06-28
Anthropic credit-exhaustion silent-degradation incident).

Pins:

  * Writes a CRITICAL alert when the credit-low pattern appears in
    journalctl
  * Does NOT write when no matches found
  * Dedupes when an UNRESOLVED matching alert already exists in
    the last 24h
  * Does NOT dedupe when the existing matching alert is OLDER than
    24h (genuine new incident)
  * Does NOT dedupe when the existing matching alert is RESOLVED
    (operator-acked previous incident; new occurrence is fresh)
  * Summary dict has the expected 4 keys
  * Fail-OPEN on DB failure
  * Fail-OPEN on journalctl failure
  * --dry-run path runs the scan but DOES NOT execute the INSERT
  * Default window_minutes is 60 (matches the 15-min timer with 4x
    headroom)
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

# ── helpers ─────────────────────────────────────────────────────────


def _journal_proc(*, stdout: str = "", returncode: int = 0):
    """Build a mock CompletedProcess for journalctl."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = ""
    return proc


def _journal_with_n_matches(n: int) -> str:
    """Build a journalctl stdout payload with N lines that contain
    the credit-low pattern + some unrelated noise lines."""
    lines = [
        "Jun 28 09:00:00 host genlab-pipeline-gaming[123]: starting",
    ]
    for i in range(n):
        lines.append(
            f"Jun 28 09:0{i}:01 host genlab-pipeline-gaming[123]: "
            f"WARNING credit balance is too low to access the Anthropic API"
        )
    lines.append("Jun 28 09:10:00 host genlab-pipeline-gaming[123]: done")
    return "\n".join(lines)


def _fake_conn(*, dedupe_rows: list | None = None) -> MagicMock:
    """Build a mock psycopg conn whose SELECT for the dedupe query
    returns ``dedupe_rows`` (default: empty list = no dedupe hit).

    The same conn handles the INSERT via .cursor() → .execute()."""
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False

    select_cursor = MagicMock()
    select_cursor.fetchall.return_value = dedupe_rows or []
    conn.execute.return_value = select_cursor

    cursor_cm = MagicMock()
    cursor_cm.__enter__.return_value = cursor_cm
    cursor_cm.__exit__.return_value = False
    conn.cursor.return_value = cursor_cm

    conn._write_cursor = cursor_cm
    conn._select_cursor = select_cursor
    return conn


# ── happy path: writes alert when pattern present ──────────────────


def test_writes_alert_when_pattern_in_journal():
    """Two occurrences of the credit-low phrase in journalctl + no
    existing UNRESOLVED alert → INSERT a CRITICAL row."""
    from genlab_core.monitoring import anthropic_credit_monitor

    proc = _journal_proc(stdout=_journal_with_n_matches(2))
    fake_conn = _fake_conn()

    with (
        patch.object(anthropic_credit_monitor, "_connect", return_value=fake_conn),
        patch.object(anthropic_credit_monitor.subprocess, "run", return_value=proc),
    ):
        summary = anthropic_credit_monitor.scan_and_alert_on_credit_exhaustion()

    assert summary["matches_found"] == 2
    assert summary["alert_written"] is True
    assert summary["dedupe_skip"] is False
    assert summary["errors"] == 0

    # The INSERT actually fired against the write cursor
    insert_calls = [
        c
        for c in fake_conn._write_cursor.execute.call_args_list
        if "INSERT INTO pipeline_alerts" in (c.args[0] if c.args else "")
    ]
    assert len(insert_calls) == 1
    insert_sql, bound = insert_calls[0].args
    assert "anthropic_credit_exhausted" in bound
    assert "critical" in bound
    assert "all" in bound
    # Message contains the operator-actionable text + billing URL
    message = bound[3]
    assert "credit balance exhausted" in message
    assert "console.anthropic.com/settings/billing" in message


# ── no match → no DB write ─────────────────────────────────────────


def test_no_match_no_alert():
    """journalctl returns no matches → no DB SELECT, no INSERT,
    summary cleanly reports 0 matches."""
    from genlab_core.monitoring import anthropic_credit_monitor

    proc = _journal_proc(stdout="Jun 28 09:00:00 host genlab[123]: nothing to see here")

    with (
        patch.object(anthropic_credit_monitor, "_connect") as mock_connect,
        patch.object(anthropic_credit_monitor.subprocess, "run", return_value=proc),
    ):
        summary = anthropic_credit_monitor.scan_and_alert_on_credit_exhaustion()

    assert summary["matches_found"] == 0
    assert summary["alert_written"] is False
    assert summary["dedupe_skip"] is False
    assert summary["errors"] == 0
    # No DB connection attempted when there's nothing to do
    assert mock_connect.call_count == 0


# ── dedupe: existing unresolved <24h ───────────────────────────────


def test_dedupe_skips_when_alert_already_today():
    """Existing UNRESOLVED 'anthropic_credit_exhausted' row in the
    last 24h → skip the INSERT (banner already showing this alert)."""
    from genlab_core.monitoring import anthropic_credit_monitor

    proc = _journal_proc(stdout=_journal_with_n_matches(3))
    # Simulate one existing row from the dedupe SELECT
    fake_conn = _fake_conn(dedupe_rows=[{"id": "existing-row-uuid"}])

    with (
        patch.object(anthropic_credit_monitor, "_connect", return_value=fake_conn),
        patch.object(anthropic_credit_monitor.subprocess, "run", return_value=proc),
    ):
        summary = anthropic_credit_monitor.scan_and_alert_on_credit_exhaustion()

    assert summary["matches_found"] == 3
    assert summary["alert_written"] is False
    assert summary["dedupe_skip"] is True
    assert summary["errors"] == 0

    # No INSERT — only the SELECT for the dedupe check fired
    insert_calls = [
        c
        for c in fake_conn._write_cursor.execute.call_args_list
        if "INSERT INTO pipeline_alerts" in (c.args[0] if c.args else "")
    ]
    assert insert_calls == []


# ── dedupe does NOT skip when row >24h old ─────────────────────────


def test_dedupe_does_NOT_skip_when_alert_older_than_24h():
    """The dedupe SELECT in the library uses
    ``created_at > NOW() - INTERVAL '24 hours'`` — rows older than
    that don't appear in the result set, so the library inserts a
    fresh row. We verify by simulating the SELECT returning empty
    (which is what the DB would return for a >24h-old row)."""
    from genlab_core.monitoring import anthropic_credit_monitor

    proc = _journal_proc(stdout=_journal_with_n_matches(1))
    # Empty result = no row in the 24h window (older rows excluded
    # at the SQL level)
    fake_conn = _fake_conn(dedupe_rows=[])

    with (
        patch.object(anthropic_credit_monitor, "_connect", return_value=fake_conn),
        patch.object(anthropic_credit_monitor.subprocess, "run", return_value=proc),
    ):
        summary = anthropic_credit_monitor.scan_and_alert_on_credit_exhaustion()

    # A new row IS written
    assert summary["alert_written"] is True
    assert summary["dedupe_skip"] is False

    insert_calls = [
        c
        for c in fake_conn._write_cursor.execute.call_args_list
        if "INSERT INTO pipeline_alerts" in (c.args[0] if c.args else "")
    ]
    assert len(insert_calls) == 1


def test_dedupe_does_NOT_skip_when_existing_alert_resolved():
    """The dedupe SELECT uses ``resolved_at IS NULL`` — resolved
    rows don't appear in the result, so a new occurrence yields a
    fresh CRITICAL row. Verified the same way as the 24h test."""
    from genlab_core.monitoring import anthropic_credit_monitor

    proc = _journal_proc(stdout=_journal_with_n_matches(1))
    # Empty result = no UNRESOLVED row (the SELECT filters out the
    # resolved row at SQL level)
    fake_conn = _fake_conn(dedupe_rows=[])

    with (
        patch.object(anthropic_credit_monitor, "_connect", return_value=fake_conn),
        patch.object(anthropic_credit_monitor.subprocess, "run", return_value=proc),
    ):
        summary = anthropic_credit_monitor.scan_and_alert_on_credit_exhaustion()

    assert summary["alert_written"] is True
    assert summary["dedupe_skip"] is False


def test_dedupe_select_filters_unresolved_and_24h():
    """Pin the actual SELECT shape so a future refactor can't silently
    weaken the dedupe semantics. The SQL must filter on both
    ``resolved_at IS NULL`` and ``created_at > NOW() - INTERVAL '24
    hours'``."""
    from genlab_core.monitoring import anthropic_credit_monitor

    proc = _journal_proc(stdout=_journal_with_n_matches(1))
    fake_conn = _fake_conn(dedupe_rows=[])

    with (
        patch.object(anthropic_credit_monitor, "_connect", return_value=fake_conn),
        patch.object(anthropic_credit_monitor.subprocess, "run", return_value=proc),
    ):
        anthropic_credit_monitor.scan_and_alert_on_credit_exhaustion()

    # The dedupe SELECT fires via conn.execute (not via the cursor)
    select_calls = [
        c
        for c in fake_conn.execute.call_args_list
        if "SELECT" in (c.args[0] if c.args else "")
        and "pipeline_alerts" in (c.args[0] if c.args else "")
    ]
    assert len(select_calls) == 1
    select_sql = select_calls[0].args[0]
    assert "resolved_at IS NULL" in select_sql
    assert "anthropic_credit_exhausted" in select_sql
    assert "INTERVAL '24 hours'" in select_sql


# ── summary dict shape ─────────────────────────────────────────────


def test_summary_dict_shape():
    """The return value must have all 4 documented counter keys —
    callers (CLI wrapper, log aggregation) depend on the shape."""
    from genlab_core.monitoring import anthropic_credit_monitor

    proc = _journal_proc(stdout="")

    with patch.object(anthropic_credit_monitor.subprocess, "run", return_value=proc):
        summary = anthropic_credit_monitor.scan_and_alert_on_credit_exhaustion()

    assert set(summary.keys()) == {
        "matches_found",
        "alert_written",
        "dedupe_skip",
        "errors",
    }
    assert isinstance(summary["matches_found"], int)
    assert isinstance(summary["alert_written"], bool)
    assert isinstance(summary["dedupe_skip"], bool)
    assert isinstance(summary["errors"], int)


# ── fail-OPEN: DB unavailable / write failure ──────────────────────


def test_fail_open_on_db_failure():
    """psycopg.connect raising → log + errors=1 but NO raise into the
    caller. The whole point of this monitor is to ADD signal, not
    remove it; a DB blip must not crash the sweep."""
    from genlab_core.monitoring import anthropic_credit_monitor

    proc = _journal_proc(stdout=_journal_with_n_matches(2))

    # _connect() returns a context manager whose __enter__ raises
    bad_conn = MagicMock()
    bad_conn.__enter__.side_effect = RuntimeError("simulated DB outage")
    bad_conn.__exit__.return_value = False

    with (
        patch.object(anthropic_credit_monitor, "_connect", return_value=bad_conn),
        patch.object(anthropic_credit_monitor.subprocess, "run", return_value=proc),
    ):
        # Must NOT raise
        summary = anthropic_credit_monitor.scan_and_alert_on_credit_exhaustion()

    assert summary["matches_found"] == 2
    assert summary["alert_written"] is False
    assert summary["errors"] == 1


def test_fail_open_when_db_dsn_missing():
    """No DATABASE_URL → _connect() returns None → log + early-return.
    Does NOT count as an error (config state, not a runtime fault)."""
    from genlab_core.monitoring import anthropic_credit_monitor

    proc = _journal_proc(stdout=_journal_with_n_matches(2))

    with (
        patch.object(anthropic_credit_monitor, "_connect", return_value=None),
        patch.object(anthropic_credit_monitor.subprocess, "run", return_value=proc),
    ):
        summary = anthropic_credit_monitor.scan_and_alert_on_credit_exhaustion()

    assert summary["matches_found"] == 2
    assert summary["alert_written"] is False
    # No DSN is a config state, not an error
    assert summary["errors"] == 0


# ── fail-OPEN: journalctl failure ──────────────────────────────────


def test_fail_open_on_journalctl_failure():
    """subprocess.run raising (TimeoutExpired / OSError / etc.) →
    log + errors=1 but NO raise into the caller."""
    from genlab_core.monitoring import anthropic_credit_monitor

    with patch.object(
        anthropic_credit_monitor.subprocess,
        "run",
        side_effect=subprocess.TimeoutExpired(cmd="journalctl", timeout=30),
    ):
        # Must NOT raise
        summary = anthropic_credit_monitor.scan_and_alert_on_credit_exhaustion()

    assert summary["matches_found"] == 0
    assert summary["alert_written"] is False
    assert summary["errors"] == 1


def test_fail_open_on_journalctl_nonzero_exit():
    """journalctl returning non-zero exit (e.g. permission denied) →
    increment errors but don't raise."""
    from genlab_core.monitoring import anthropic_credit_monitor

    proc = _journal_proc(stdout="", returncode=1)
    proc.stderr = "Permission denied"

    with patch.object(anthropic_credit_monitor.subprocess, "run", return_value=proc):
        summary = anthropic_credit_monitor.scan_and_alert_on_credit_exhaustion()

    assert summary["matches_found"] == 0
    assert summary["alert_written"] is False
    assert summary["errors"] == 1


# ── --dry-run path ─────────────────────────────────────────────────


def test_dry_run_does_not_write():
    """dry_run=True → journalctl IS called, dedupe SELECT IS called,
    but the INSERT is NOT. Summary's alert_written reflects what
    WOULD have happened."""
    from genlab_core.monitoring import anthropic_credit_monitor

    proc = _journal_proc(stdout=_journal_with_n_matches(2))
    fake_conn = _fake_conn(dedupe_rows=[])

    with (
        patch.object(anthropic_credit_monitor, "_connect", return_value=fake_conn),
        patch.object(anthropic_credit_monitor.subprocess, "run", return_value=proc) as mock_run,
    ):
        summary = anthropic_credit_monitor.scan_and_alert_on_credit_exhaustion(dry_run=True)

    # journalctl IS called
    assert mock_run.call_count == 1
    # Summary reflects what WOULD have happened
    assert summary["alert_written"] is True
    # But no INSERT actually fired
    insert_calls = [
        c
        for c in fake_conn._write_cursor.execute.call_args_list
        if "INSERT INTO pipeline_alerts" in (c.args[0] if c.args else "")
    ]
    assert insert_calls == []


# ── default window_minutes ─────────────────────────────────────────


def test_window_minutes_default_is_60():
    """The default window_minutes is 60 — 4x headroom over the 15-min
    timer interval. We verify by inspecting the actual journalctl
    invocation arguments."""
    from genlab_core.monitoring import anthropic_credit_monitor

    proc = _journal_proc(stdout="")

    with patch.object(anthropic_credit_monitor.subprocess, "run", return_value=proc) as mock_run:
        anthropic_credit_monitor.scan_and_alert_on_credit_exhaustion()

    assert mock_run.call_count == 1
    cmd = mock_run.call_args.args[0]
    # Find the --since arg and verify the value is "60 minutes ago"
    assert "--since" in cmd
    idx = cmd.index("--since")
    assert cmd[idx + 1] == "60 minutes ago"


def test_window_minutes_override_propagates():
    """Operator can pass a custom window — the value must flow
    through to the journalctl --since flag."""
    from genlab_core.monitoring import anthropic_credit_monitor

    proc = _journal_proc(stdout="")

    with patch.object(anthropic_credit_monitor.subprocess, "run", return_value=proc) as mock_run:
        anthropic_credit_monitor.scan_and_alert_on_credit_exhaustion(window_minutes=120)

    cmd = mock_run.call_args.args[0]
    idx = cmd.index("--since")
    assert cmd[idx + 1] == "120 minutes ago"


# ── CLI wrapper exits 0 always ─────────────────────────────────────


def test_cli_exits_zero_on_success():
    """scripts/anthropic_credit_monitor.py must always exit 0 — the
    monitor is informational and a non-zero exit would chain into a
    confusing systemd_unit_failed alert."""
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parents[3] / "scripts" / "anthropic_credit_monitor.py"
    spec = importlib.util.spec_from_file_location("anthropic_credit_monitor_cli", script)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    with patch(
        "genlab_core.monitoring.anthropic_credit_monitor.scan_and_alert_on_credit_exhaustion",
        return_value={
            "matches_found": 5,
            "alert_written": True,
            "dedupe_skip": False,
            "errors": 0,
        },
    ):
        assert mod.main([]) == 0


def test_cli_exits_zero_with_dry_run_flag():
    """--dry-run must still exit 0 cleanly + pass dry_run=True
    through to the library."""
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parents[3] / "scripts" / "anthropic_credit_monitor.py"
    spec = importlib.util.spec_from_file_location("anthropic_credit_monitor_cli2", script)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    captured = {}

    def fake_scanner(*, window_minutes=60, dry_run=False):
        captured["window_minutes"] = window_minutes
        captured["dry_run"] = dry_run
        return {
            "matches_found": 0,
            "alert_written": False,
            "dedupe_skip": False,
            "errors": 0,
        }

    with patch(
        "genlab_core.monitoring.anthropic_credit_monitor.scan_and_alert_on_credit_exhaustion",
        side_effect=fake_scanner,
    ):
        assert mod.main(["--dry-run", "--window-minutes", "30"]) == 0
        assert captured["dry_run"] is True
        assert captured["window_minutes"] == 30
