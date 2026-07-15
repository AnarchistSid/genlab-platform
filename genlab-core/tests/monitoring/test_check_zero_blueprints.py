"""Tests for ``genlab_core.monitoring.checks.pipeline.check_zero_blueprints``.

Task #622 (2026-07-09) — suppress the alert when the niche's forward
queue already has ≥3 blueprints scheduled_for the next 5 days. Sports
hit this false positive 3 times on 2026-07-09: pipeline correctly ran,
produced 0 new blueprints (dedup found no new eligible clips), but
had blueprints for 07-11 through 07-14. Each health_monitor fire
re-detected the "0 new" state and re-wrote a CRITICAL alert.

Pins:

  * 0 consecutive zero → never alert (unchanged existing behavior)
  * Consecutive zero + queue < 3 → alert fires (real content gap)
  * Consecutive zero + queue ≥ 3 → SUPPRESS (the #622 fix)
  * Queue-depth query DB error → fail-open (alert fires anyway; safer
    to over-alert than hide a real content gap on infra failure)
  * Alert details payload includes ``forward_queue_depth`` for
    dashboard observability
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from genlab_core.monitoring.checks.pipeline import (
    _QUEUE_RUNWAY_MIN_BLUEPRINTS,
    _QUEUE_RUNWAY_SUPPRESSION_DAYS,
    check_zero_blueprints,
)


def _report(*, blueprints: int, stories: int) -> dict:
    """Build a minimal report dict matching the shape check_zero_blueprints reads."""
    return {
        "metrics": {
            "blueprints_count": blueprints,
            "stories_count": stories,
        },
        "_run_dir": "",
    }


def _mk_queue_conn(row_count: int) -> MagicMock:
    """Build a fake pg_connect context manager returning a canned COUNT."""
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    cursor = MagicMock()
    # 2026-07-15: pg_connect returns positional tuples by default.
    # Prior mock `{"n": row_count}` silently masked the prod bug —
    # `row["n"]` raised TypeError, caught by broad except → returned
    # None → queue-depth suppression silently disabled for weeks
    # (surfaced post-2026-07-15 deploy). Fix uses `row[0]`; mock
    # matches.
    cursor.fetchone.return_value = (row_count,)
    conn.execute.return_value = cursor
    return conn


# ── existing behavior: no zero runs → no alert ────────────────────


def test_no_zero_runs_returns_no_alert():
    """When there are no consecutive zero-blueprint runs, no alert fires
    regardless of queue depth. Guards against changes that would break
    the trivial healthy-state case."""
    reports = [_report(blueprints=3, stories=5)]
    alerts = check_zero_blueprints(reports, "sports")
    assert alerts == []


# ── the fix: queue-full suppression ──────────────────────────────


def test_queue_at_threshold_suppresses_alert():
    """Task #622 core pin: 2 consecutive zero-blueprint runs AND
    queue has exactly the minimum (3) → alert MUST be suppressed.

    Rationale: this is the exact 2026-07-09 sports scenario — 4
    future-scheduled blueprints + healthy pipeline that correctly
    made no new content because upcoming slots are covered."""
    reports = [
        _report(blueprints=0, stories=5),
        _report(blueprints=0, stories=5),
    ]

    with patch(
        "genlab_core.monitoring.checks.pipeline.pg_connect",
        return_value=_mk_queue_conn(_QUEUE_RUNWAY_MIN_BLUEPRINTS),
    ):
        alerts = check_zero_blueprints(reports, "sports")

    assert alerts == [], (
        f"Queue depth {_QUEUE_RUNWAY_MIN_BLUEPRINTS} meets threshold — "
        "alert must be suppressed. The pipeline is healthy; the queue "
        "already covers upcoming slots."
    )


def test_queue_above_threshold_suppresses_alert():
    """Queue depth = 5 (above threshold of 3) also suppresses."""
    reports = [_report(blueprints=0, stories=5)]

    with patch(
        "genlab_core.monitoring.checks.pipeline.pg_connect",
        return_value=_mk_queue_conn(5),
    ):
        alerts = check_zero_blueprints(reports, "sports")

    assert alerts == []


def test_queue_below_threshold_still_alerts():
    """When queue is <3 (e.g. 2 blueprints for next 5 days), the alert
    MUST still fire. This is the real content-gap case — pipeline
    isn't producing AND the queue is running low."""
    reports = [
        _report(blueprints=0, stories=5),
        _report(blueprints=0, stories=5),
    ]

    with (
        patch(
            "genlab_core.monitoring.checks.pipeline.pg_connect",
            return_value=_mk_queue_conn(2),
        ),
        patch(
            "genlab_core.monitoring.health_monitor._load_clip_index",
            return_value={},
        ),
    ):
        alerts = check_zero_blueprints(reports, "sports")

    assert len(alerts) == 1, (
        "Queue below threshold — alert MUST still fire. Suppressing "
        "this case would hide a real content-gap regression."
    )
    assert alerts[0].check == "zero_blueprints"
    # 2 consecutive zeros → critical severity
    assert alerts[0].severity == "critical"
    # And observability: the queue depth is in details for the operator
    # to see WHY the check chose not to suppress.
    assert alerts[0].details["forward_queue_depth"] == 2


def test_queue_zero_absolutely_alerts():
    """Extreme case: queue is empty AND pipeline making 0 → definitely
    alert. This is the operationally worst-case; the check must not
    accidentally hide it."""
    reports = [_report(blueprints=0, stories=5)]

    with (
        patch(
            "genlab_core.monitoring.checks.pipeline.pg_connect",
            return_value=_mk_queue_conn(0),
        ),
        patch(
            "genlab_core.monitoring.health_monitor._load_clip_index",
            return_value={},
        ),
    ):
        alerts = check_zero_blueprints(reports, "gaming")

    assert len(alerts) == 1
    assert alerts[0].details["forward_queue_depth"] == 0


# ── fail-open on infra error ─────────────────────────────────────


def test_db_error_falls_through_to_alert():
    """When the queue-depth query raises, we still fire the alert.
    Fail-OPEN: better to over-alert than to silently hide a real
    content-gap because Postgres briefly unavailable."""
    reports = [_report(blueprints=0, stories=5)]

    def _raise(*args, **kwargs):
        raise ConnectionError("pg down")

    with (
        patch(
            "genlab_core.monitoring.checks.pipeline.pg_connect",
            side_effect=_raise,
        ),
        patch(
            "genlab_core.monitoring.health_monitor._load_clip_index",
            return_value={},
        ),
    ):
        alerts = check_zero_blueprints(reports, "sports")

    assert len(alerts) == 1, (
        "DB error must not hide the alert. Fail-OPEN discipline: "
        "surface content-gap even when queue-depth check can't run."
    )
    # forward_queue_depth is None to distinguish "unknown" from "0"
    assert alerts[0].details["forward_queue_depth"] is None


# ── config constants ─────────────────────────────────────────────


def test_suppression_days_is_5():
    """5-day window matches the operator's operational cadence — one
    working week. Lower risks false-negatives (miss a real regression);
    higher makes the suppression too generous."""
    assert _QUEUE_RUNWAY_SUPPRESSION_DAYS == 5


def test_min_blueprints_threshold_is_3():
    """3 blueprints = 3 days of daily-cadence runway. Buffer for
    weekend + one bad-fetch day. Changing this without live-fire
    justification would either re-open the sports 2026-07-09 false
    positive (raise to 5+) or hide real gaps (lower to 1)."""
    assert _QUEUE_RUNWAY_MIN_BLUEPRINTS == 3
