"""2026-06-14 (PR #200): hook-trainer must emit a pipeline_alerts row
when training fails, not just ERROR-log.

Audit finding: the trainer has been failing weekly since 2026-05-21
with "Permission denied" writing the model JSON. Each failure logged
ERROR but nothing surfaced to the operator because pipeline_alerts
wasn't populated. 24 days of silent training failure → 691 unused
labeled examples sitting in pending_feedback.

These pins guarantee future failures DO surface, even though the
underlying permission issue is fixed by the systemd unit ExecStartPre
chown (also part of PR #200).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _mock_pg_no_existing_alert():
    """psycopg.connect mock: cursor returns no existing alert (so the
    insert path fires) and the insert succeeds."""
    cur = MagicMock()
    cur.fetchone.return_value = None  # no dedup hit
    cur.__enter__ = lambda self: self
    cur.__exit__ = lambda self, *a: None
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.__enter__ = lambda self: self
    conn.__exit__ = lambda self, *a: None
    return conn, cur


def test_alert_inserted_with_actionable_message(monkeypatch):
    """Alert message must include the niche_id, the error excerpt, AND
    the remediation hint pointing at the ExecStartPre chown fix."""
    from genlab_core.learning.hook_classifier import _emit_training_failure_alert

    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    conn, cur = _mock_pg_no_existing_alert()

    with patch("psycopg.connect", return_value=conn):
        _emit_training_failure_alert("gaming", "Permission denied: hook_classifier_gaming.json")

    # First call was the dedup SELECT; second was the INSERT.
    assert cur.execute.call_count == 2
    insert_sql, insert_params = cur.execute.call_args.args
    assert "INSERT INTO pipeline_alerts" in insert_sql

    niche, check, severity, message, _details = insert_params
    assert niche == "gaming"
    assert check == "hook_training_failed"
    assert severity == "warning"
    assert "gaming" in message
    assert "Permission denied" in message
    assert "PR #200" in message  # references the fix that landed alongside


def test_alert_dedups_against_existing_unresolved_alert(monkeypatch):
    """If an unresolved alert already exists for (niche, check), DON'T
    pile on another. Matches the existing token_expired dedup pattern."""
    from genlab_core.learning.hook_classifier import _emit_training_failure_alert

    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    cur = MagicMock()
    cur.fetchone.return_value = ("existing-alert-id",)  # dedup hit
    cur.__enter__ = lambda self: self
    cur.__exit__ = lambda self, *a: None
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.__enter__ = lambda self: self
    conn.__exit__ = lambda self, *a: None

    with patch("psycopg.connect", return_value=conn):
        _emit_training_failure_alert("gaming", "some error")

    # Only the dedup SELECT; no INSERT call.
    assert cur.execute.call_count == 1
    sql = cur.execute.call_args.args[0]
    assert "SELECT" in sql


def test_alert_no_op_when_database_url_missing(monkeypatch):
    """Dev box / test runner has no DATABASE_URL — the alert helper
    must return silently. Critical: must NOT raise into the training
    caller (which would crash the whole nightly run)."""
    from genlab_core.learning.hook_classifier import _emit_training_failure_alert

    monkeypatch.delenv("DATABASE_URL", raising=False)
    with patch("psycopg.connect") as mock_connect:
        _emit_training_failure_alert("gaming", "any error")
    mock_connect.assert_not_called()


def test_alert_swallows_db_errors_silently(monkeypatch):
    """If psycopg.connect itself raises (network failure, auth error),
    the alert helper must NOT re-raise. The training caller's loop
    over niches must continue."""
    from genlab_core.learning.hook_classifier import _emit_training_failure_alert

    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    with patch("psycopg.connect", side_effect=RuntimeError("conn refused")):
        # Must NOT raise. If this asserts, the regression is back.
        _emit_training_failure_alert("gaming", "any error")
