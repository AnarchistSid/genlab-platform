"""Tests for ``observability.alert_auto_resolver`` (PR for 2026-06-27
deploy-restart-sweep + stale-alerts incident).

Pins:

  * Resolves the row when the unit has had a successful run AFTER
    the alert was created (the happy path)
  * Does NOT resolve when the unit is currently active (race-condition
    safety)
  * Does NOT resolve when the unit is still in failed state
  * Does NOT resolve when there's no success since the alert
    (InactiveEnterTimestamp <= created_at)
  * Increments errors counter when systemctl-show fails (unit not
    found / subprocess timeout)
  * Increments errors counter when the message can't be parsed into
    a unit name (unparseable row)
  * --dry-run path increments resolved counter without calling
    cursor.execute("UPDATE ...")
  * Counter dict is correctly populated across mixed cases
  * Per-row exception does not abort the loop (fail-open)
  * Empty SELECT returns zero counters cleanly
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4


def _row(message: str, created_at: datetime, row_id=None) -> dict:
    return {
        "id": row_id or uuid4(),
        "message": message,
        "created_at": created_at,
    }


def _build_message(unit: str) -> str:
    """Mirror scripts/systemd_failure_alert.sh's MESSAGE shape."""
    return (
        f"Systemd unit {unit} failed at 2026-06-27T09:30:00Z.\n"
        f"Investigate with: systemctl status {unit} && journalctl -u {unit} -n 50"
    )


def _systemd_show_proc(
    *,
    result: str,
    exec_main_status: str,
    inactive_enter: str,
    active_state: str,
    returncode: int = 0,
):
    """Build a mock subprocess.CompletedProcess for systemctl-show."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = "\n".join(
        [
            f"Result={result}",
            f"ExecMainStatus={exec_main_status}",
            f"InactiveEnterTimestamp={inactive_enter}",
            f"ActiveState={active_state}",
        ]
    )
    proc.stderr = ""
    return proc


def _fake_conn(select_rows: list[dict]) -> MagicMock:
    """Build a mock psycopg conn whose execute() returns a fake cursor
    that fetchall() yields ``select_rows``. The mock is reused for the
    UPDATE conn open too (separate ``with`` block in the resolver)."""
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False

    select_cursor = MagicMock()
    select_cursor.fetchall.return_value = select_rows
    conn.execute.return_value = select_cursor

    cursor_cm = MagicMock()
    cursor_cm.__enter__.return_value = cursor_cm
    cursor_cm.__exit__.return_value = False
    conn.cursor.return_value = cursor_cm

    # Expose the cursor that handles writes for assertion convenience.
    conn._write_cursor = cursor_cm
    conn._select_cursor = select_cursor
    return conn


# ── happy path: resolves when unit succeeded after alert ────────────


def test_resolves_when_unit_succeeded_after_alert():
    from genlab_core.observability import alert_auto_resolver

    created = datetime(2026, 6, 27, 9, 30, 0, tzinfo=UTC)
    success_at = "Sat 2026-06-27 09:35:00 UTC"
    row = _row(_build_message("genlab-token-refresh.service"), created)

    fake_conn = _fake_conn([row])

    proc = _systemd_show_proc(
        result="success",
        exec_main_status="0",
        inactive_enter=success_at,
        active_state="inactive",
    )

    with (
        patch.object(alert_auto_resolver, "_connect", return_value=fake_conn),
        patch.object(alert_auto_resolver.subprocess, "run", return_value=proc) as mock_run,
    ):
        counters = alert_auto_resolver.auto_resolve_systemd_unit_alerts()

    assert counters["checked"] == 1
    assert counters["resolved"] == 1
    assert counters["skipped_still_failed"] == 0
    assert counters["skipped_no_recent_run"] == 0
    assert counters["errors"] == 0

    # systemctl-show called for the right unit
    assert mock_run.call_count == 1
    args = mock_run.call_args
    cmd = args.args[0]
    assert "systemctl" in cmd
    assert "show" in cmd
    assert "genlab-token-refresh.service" in cmd

    # UPDATE actually executed — pull out the SQL fired against the
    # write cursor (separate from the SELECT cursor on the same conn).
    update_calls = [
        c
        for c in fake_conn._write_cursor.execute.call_args_list
        if "UPDATE pipeline_alerts" in (c.args[0] if c.args else "")
    ]
    assert len(update_calls) == 1
    update_sql = update_calls[0].args[0]
    assert "resolved_at = NOW()" in update_sql
    # The row id must be the second bound parameter
    bound = update_calls[0].args[1]
    assert bound[1] == row["id"]
    # Audit-trail note in first bound parameter
    assert "AUTO-RESOLVED" in bound[0]
    assert "Result=success" in bound[0]


# ── skip: unit is still failed ─────────────────────────────────────


def test_does_not_resolve_when_unit_still_failed():
    from genlab_core.observability import alert_auto_resolver

    created = datetime(2026, 6, 27, 9, 30, 0, tzinfo=UTC)
    row = _row(_build_message("genlab-health-monitor.service"), created)
    fake_conn = _fake_conn([row])

    proc = _systemd_show_proc(
        result="failed",
        exec_main_status="1",
        inactive_enter="Sat 2026-06-27 09:35:00 UTC",
        active_state="inactive",
    )

    with (
        patch.object(alert_auto_resolver, "_connect", return_value=fake_conn),
        patch.object(alert_auto_resolver.subprocess, "run", return_value=proc),
    ):
        counters = alert_auto_resolver.auto_resolve_systemd_unit_alerts()

    assert counters["checked"] == 1
    assert counters["resolved"] == 0
    assert counters["skipped_still_failed"] == 1
    # No UPDATE fired
    update_calls = [
        c
        for c in fake_conn._write_cursor.execute.call_args_list
        if "UPDATE pipeline_alerts" in (c.args[0] if c.args else "")
    ]
    assert update_calls == []


# ── skip: no success since alert ───────────────────────────────────


def test_does_not_resolve_when_no_run_since_alert():
    """InactiveEnterTimestamp is BEFORE alert.created_at → no recovery
    observed yet. Skip and wait for the next sweep."""
    from genlab_core.observability import alert_auto_resolver

    created = datetime(2026, 6, 27, 9, 30, 0, tzinfo=UTC)
    row = _row(_build_message("genlab-pipeline-gaming.service"), created)
    fake_conn = _fake_conn([row])

    # success_at is 5 min BEFORE the alert
    proc = _systemd_show_proc(
        result="success",
        exec_main_status="0",
        inactive_enter="Sat 2026-06-27 09:25:00 UTC",
        active_state="inactive",
    )

    with (
        patch.object(alert_auto_resolver, "_connect", return_value=fake_conn),
        patch.object(alert_auto_resolver.subprocess, "run", return_value=proc),
    ):
        counters = alert_auto_resolver.auto_resolve_systemd_unit_alerts()

    assert counters["checked"] == 1
    assert counters["resolved"] == 0
    assert counters["skipped_no_recent_run"] == 1


# ── skip: unit currently active ────────────────────────────────────


def test_skips_when_unit_currently_active():
    """Race-condition safety: don't resolve while the unit is mid-run."""
    from genlab_core.observability import alert_auto_resolver

    created = datetime(2026, 6, 27, 9, 30, 0, tzinfo=UTC)
    row = _row(_build_message("genlab-archive-stale-visuals.service"), created)
    fake_conn = _fake_conn([row])

    proc = _systemd_show_proc(
        result="success",
        exec_main_status="0",
        inactive_enter="Sat 2026-06-27 09:35:00 UTC",
        active_state="active",  # ← in-flight
    )

    with (
        patch.object(alert_auto_resolver, "_connect", return_value=fake_conn),
        patch.object(alert_auto_resolver.subprocess, "run", return_value=proc),
    ):
        counters = alert_auto_resolver.auto_resolve_systemd_unit_alerts()

    assert counters["checked"] == 1
    assert counters["resolved"] == 0
    assert counters["skipped_no_recent_run"] == 1


# ── error: unit not found ──────────────────────────────────────────


def test_skips_when_unit_not_found():
    """systemctl-show returns non-zero exit when the unit doesn't
    exist. We treat as 'cannot confirm' and increment errors."""
    from genlab_core.observability import alert_auto_resolver

    created = datetime(2026, 6, 27, 9, 30, 0, tzinfo=UTC)
    row = _row(_build_message("genlab-does-not-exist.service"), created)
    fake_conn = _fake_conn([row])

    proc = MagicMock()
    proc.returncode = 1
    proc.stdout = ""
    proc.stderr = "Unit not found"

    with (
        patch.object(alert_auto_resolver, "_connect", return_value=fake_conn),
        patch.object(alert_auto_resolver.subprocess, "run", return_value=proc),
    ):
        counters = alert_auto_resolver.auto_resolve_systemd_unit_alerts()

    assert counters["checked"] == 1
    assert counters["resolved"] == 0
    assert counters["errors"] == 1


# ── error: unparseable message ─────────────────────────────────────


def test_handles_unparseable_message_text():
    """A pipeline_alerts row written by some other path (manual ops
    SQL? broken migration?) might have a message that doesn't match
    our regex. Skip + increment errors — don't crash."""
    from genlab_core.observability import alert_auto_resolver

    created = datetime(2026, 6, 27, 9, 30, 0, tzinfo=UTC)
    row = _row("Some operator wrote this manually — no unit name", created)
    fake_conn = _fake_conn([row])

    with (
        patch.object(alert_auto_resolver, "_connect", return_value=fake_conn),
        patch.object(alert_auto_resolver.subprocess, "run") as mock_run,
    ):
        counters = alert_auto_resolver.auto_resolve_systemd_unit_alerts()

    # subprocess.run should NOT have been called — we bailed at parse
    assert mock_run.call_count == 0
    assert counters["checked"] == 1
    assert counters["errors"] == 1
    assert counters["resolved"] == 0


# ── --dry-run does not UPDATE ──────────────────────────────────────


def test_dry_run_does_not_execute_update():
    from genlab_core.observability import alert_auto_resolver

    created = datetime(2026, 6, 27, 9, 30, 0, tzinfo=UTC)
    row = _row(_build_message("genlab-token-refresh.service"), created)
    fake_conn = _fake_conn([row])

    proc = _systemd_show_proc(
        result="success",
        exec_main_status="0",
        inactive_enter="Sat 2026-06-27 09:35:00 UTC",
        active_state="inactive",
    )

    with (
        patch.object(alert_auto_resolver, "_connect", return_value=fake_conn),
        patch.object(alert_auto_resolver.subprocess, "run", return_value=proc),
    ):
        counters = alert_auto_resolver.auto_resolve_systemd_unit_alerts(dry_run=True)

    # Counter reflects what WOULD have happened
    assert counters["resolved"] == 1
    # But NO UPDATE actually fired
    update_calls = [
        c
        for c in fake_conn._write_cursor.execute.call_args_list
        if "UPDATE pipeline_alerts" in (c.args[0] if c.args else "")
    ]
    assert update_calls == []


# ── mixed batch — counters correctly summed ────────────────────────


def test_returns_correct_summary_dict():
    """A realistic mixed batch: one resolvable, one still-failed, one
    active, one unparseable. Counters must sum cleanly."""
    from genlab_core.observability import alert_auto_resolver

    created = datetime(2026, 6, 27, 9, 30, 0, tzinfo=UTC)
    rows = [
        _row(_build_message("genlab-resolvable.service"), created),
        _row(_build_message("genlab-still-failed.service"), created),
        _row(_build_message("genlab-active.service"), created),
        _row("Unparseable garbage", created),
    ]
    fake_conn = _fake_conn(rows)

    def fake_show(args, **kw):
        # Map unit name → response
        unit = args[2]  # ["systemctl", "show", "<unit>", ...]
        if unit == "genlab-resolvable.service":
            return _systemd_show_proc(
                result="success",
                exec_main_status="0",
                inactive_enter="Sat 2026-06-27 09:35:00 UTC",
                active_state="inactive",
            )
        if unit == "genlab-still-failed.service":
            return _systemd_show_proc(
                result="failed",
                exec_main_status="1",
                inactive_enter="Sat 2026-06-27 09:35:00 UTC",
                active_state="inactive",
            )
        if unit == "genlab-active.service":
            return _systemd_show_proc(
                result="success",
                exec_main_status="0",
                inactive_enter="Sat 2026-06-27 09:35:00 UTC",
                active_state="active",
            )
        # Unparseable rows shouldn't reach subprocess.run
        raise AssertionError(f"unexpected unit: {unit}")

    with (
        patch.object(alert_auto_resolver, "_connect", return_value=fake_conn),
        patch.object(alert_auto_resolver.subprocess, "run", side_effect=fake_show),
    ):
        counters = alert_auto_resolver.auto_resolve_systemd_unit_alerts()

    assert counters["checked"] == 4
    assert counters["resolved"] == 1
    assert counters["skipped_still_failed"] == 1
    assert counters["skipped_no_recent_run"] == 1
    assert counters["errors"] == 1


# ── per-row exception does not abort the sweep ─────────────────────


def test_per_row_exception_does_not_abort():
    """One row raising must NOT block the rest — fail-OPEN per-row.

    We simulate this by making subprocess.run raise on the first call
    and succeed on the second; the second row should still be
    processed (and resolved) cleanly."""
    from genlab_core.observability import alert_auto_resolver

    created = datetime(2026, 6, 27, 9, 30, 0, tzinfo=UTC)
    rows = [
        _row(_build_message("genlab-raises.service"), created),
        _row(_build_message("genlab-ok.service"), created),
    ]
    fake_conn = _fake_conn(rows)

    call_count = {"n": 0}

    def fake_show(args, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated unexpected error")
        return _systemd_show_proc(
            result="success",
            exec_main_status="0",
            inactive_enter="Sat 2026-06-27 09:35:00 UTC",
            active_state="inactive",
        )

    with (
        patch.object(alert_auto_resolver, "_connect", return_value=fake_conn),
        patch.object(alert_auto_resolver.subprocess, "run", side_effect=fake_show),
    ):
        counters = alert_auto_resolver.auto_resolve_systemd_unit_alerts()

    assert counters["checked"] == 2
    # The second row still got resolved despite the first one blowing up
    assert counters["resolved"] == 1
    assert counters["errors"] == 1


# ── empty input returns zero counters ──────────────────────────────


def test_no_unresolved_alerts_returns_zero():
    from genlab_core.observability import alert_auto_resolver

    fake_conn = _fake_conn([])

    with (
        patch.object(alert_auto_resolver, "_connect", return_value=fake_conn),
        patch.object(alert_auto_resolver.subprocess, "run") as mock_run,
    ):
        counters = alert_auto_resolver.auto_resolve_systemd_unit_alerts()

    assert counters == {
        "checked": 0,
        "resolved": 0,
        "skipped_still_failed": 0,
        "skipped_no_recent_run": 0,
        "errors": 0,
    }
    # Never called systemctl-show on empty input
    assert mock_run.call_count == 0


# ── DB connection unavailable fails open ───────────────────────────


def test_returns_zero_counters_when_db_unavailable():
    """No DSN / connection failure → log + return zero counters; the
    timer's next fire will retry. Never raises into the caller."""
    from genlab_core.observability import alert_auto_resolver

    with patch.object(alert_auto_resolver, "_connect", return_value=None):
        counters = alert_auto_resolver.auto_resolve_systemd_unit_alerts()

    assert counters == {
        "checked": 0,
        "resolved": 0,
        "skipped_still_failed": 0,
        "skipped_no_recent_run": 0,
        "errors": 0,
    }


# ── CLI wrapper exits 0 always ─────────────────────────────────────


def test_cli_exits_zero_on_success():
    """scripts/auto_resolve_alerts.py must always exit 0 — the sweeper
    is informational and a non-zero exit would fire its OWN systemd
    failure alert (infinite regress)."""
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parents[3] / "scripts" / "auto_resolve_alerts.py"
    spec = importlib.util.spec_from_file_location("auto_resolve_alerts_cli", script)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    with patch(
        "genlab_core.observability.alert_auto_resolver.auto_resolve_systemd_unit_alerts",
        return_value={
            "checked": 5,
            "resolved": 3,
            "skipped_still_failed": 1,
            "skipped_no_recent_run": 1,
            "errors": 0,
        },
    ):
        assert mod.main([]) == 0


def test_cli_exits_zero_even_with_dry_run_flag():
    """--dry-run must still exit 0 cleanly."""
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parents[3] / "scripts" / "auto_resolve_alerts.py"
    spec = importlib.util.spec_from_file_location("auto_resolve_alerts_cli2", script)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    captured = {}

    def fake_resolver(*, dry_run=False):
        captured["dry_run"] = dry_run
        return {
            "checked": 0,
            "resolved": 0,
            "skipped_still_failed": 0,
            "skipped_no_recent_run": 0,
            "errors": 0,
        }

    with patch(
        "genlab_core.observability.alert_auto_resolver.auto_resolve_systemd_unit_alerts",
        side_effect=fake_resolver,
    ):
        assert mod.main(["--dry-run"]) == 0
        assert captured["dry_run"] is True
