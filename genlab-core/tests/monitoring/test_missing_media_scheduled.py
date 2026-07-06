"""R-79 regression: check_missing_media must never auto-archive a scheduled post.

cleanup_safety.md: "NEVER demote, delete, or clear data on blueprints that have
a scheduled_for date." The missing-media auto-fix previously selected
VISUAL_READY rows with no scheduled_for filter and archived them via raw SQL,
bypassing ScheduleGuardedProxy — so a transient mount/symlink miss could archive
a scheduled post. The fix archives only unscheduled broken blueprints and
surfaces scheduled-broken ones as an alert instead.

2026-07-06 refinement (task #535): sacred-post protection still applies, but
scheduled-broken blueprints whose publish window closed >24h ago downgrade to
a distinct `missing_media_past_due` WARNING alert to prevent CRITICAL noise
on stuck state that hasn't changed for days. Data is still not touched.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch


def test_missing_media_never_archives_scheduled_posts(tmp_path) -> None:
    from genlab_core.monitoring.health_monitor import check_missing_media

    # SAFETY GATE 2 requires GENLAB_PROJECT_ROOT/.tmp to exist.
    (tmp_path / ".tmp").mkdir()
    good_file = tmp_path / "good.mp4"
    good_file.write_text("x")
    missing = str(tmp_path / "gone.mp4")

    # FUTURE scheduled — publish window still live, must be treated as sacred.
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    # (id, title, visual_paths_json, scheduled_for)
    mock_cur.fetchall.return_value = [
        ("good-bp", "ok", f'["{good_file}"]', None),  # file exists -> not broken
        ("unsched-bp", "broken", f'["{missing}"]', None),  # broken + unscheduled -> archive
        (
            "sched-bp",
            "broken",
            f'["{missing}"]',
            future,
        ),  # broken + FUTURE scheduled -> protect (R-79)
    ]

    with patch.dict(
        os.environ,
        {"DATABASE_URL": "postgresql://fake", "GENLAB_PROJECT_ROOT": str(tmp_path)},
    ):
        with patch("psycopg.connect", return_value=mock_conn):
            alerts = check_missing_media("gaming")

    # The archive UPDATE must run exactly once and target ONLY the unscheduled id.
    update_calls = [
        c
        for c in mock_cur.execute.call_args_list
        if "UPDATE blueprints SET status = 'ARCHIVED'" in c.args[0]
    ]
    assert len(update_calls) == 1, "expected exactly one archive UPDATE"
    archived_ids = update_calls[0].args[1][0]
    assert "unsched-bp" in archived_ids
    assert "sched-bp" not in archived_ids, "scheduled post must NOT be archived (cleanup_safety.md)"

    # The scheduled-broken blueprint must still be surfaced (visibility, no archive).
    assert any(a.check == "missing_media_scheduled" for a in alerts), [a.check for a in alerts]


def test_missing_media_past_due_downgrades_to_warning(tmp_path) -> None:
    """Task #535: scheduled_for > 24h in the past downgrades to WARNING alert.

    Prod caught 3 blueprints scheduled for 2026-07-03 that failed render days
    earlier and were still firing CRITICAL `missing_media_scheduled` every
    monitor tick. Publish window is dead — CRITICAL doesn't reflect actionable
    state. New `missing_media_past_due` (WARNING severity) captures them and
    leaves the CRITICAL alert lane for genuine sacred-post issues.

    R-79 still applies: past-due scheduled posts are still NOT auto-archived.
    Data untouched — only the alert taxonomy differs.
    """
    from genlab_core.monitoring.health_monitor import check_missing_media

    (tmp_path / ".tmp").mkdir()
    missing = str(tmp_path / "gone.mp4")

    # 3 days in the past — well past the 24h cutoff.
    past_due = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    # 2h in the past — still inside the 24h cutoff, so counted as sacred.
    recent_scheduled = (datetime.now(UTC) - timedelta(hours=2)).isoformat()

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    mock_cur.fetchall.return_value = [
        ("past-due-bp", "stuck", f'["{missing}"]', past_due),
        ("recent-bp", "recent", f'["{missing}"]', recent_scheduled),
    ]

    with patch.dict(
        os.environ,
        {"DATABASE_URL": "postgresql://fake", "GENLAB_PROJECT_ROOT": str(tmp_path)},
    ):
        with patch("psycopg.connect", return_value=mock_conn):
            alerts = check_missing_media("gaming")

    checks = {a.check: a for a in alerts}

    # SAFETY GATE 1: 100%-broken batch trips the mass alert BEFORE we split
    # buckets. The bucketing only fires when we reach the per-post archive
    # block, which we don't in an all-broken batch. So we assert
    # missing_media_mass fires instead (the gate that guards mount-issue
    # false-positives).
    assert "missing_media_mass" in checks, (
        "all-broken batches trip missing_media_mass before bucketing runs; "
        "this is intentional per health_monitor.py:629"
    )

    # No archive UPDATE — sacred rule still applies to both past-due AND recent.
    update_calls = [
        c
        for c in mock_cur.execute.call_args_list
        if "UPDATE blueprints SET status = 'ARCHIVED'" in c.args[0]
    ]
    assert not update_calls, "past-due scheduled posts must NOT be auto-archived (R-79)"


def test_missing_media_past_due_bucketing_when_healthy_posts_exist(tmp_path) -> None:
    """Same fix, but with a healthy post in the batch so SAFETY GATE 1 doesn't
    trip and the per-bucket splitter actually runs. Pin the two-bucket path:
    past-due-broken → WARNING, recent-scheduled-broken → CRITICAL sacred.
    """
    from genlab_core.monitoring.health_monitor import check_missing_media

    (tmp_path / ".tmp").mkdir()
    good_file = tmp_path / "good.mp4"
    good_file.write_text("x")
    good_file2 = tmp_path / "good2.mp4"
    good_file2.write_text("y")
    good_file3 = tmp_path / "good3.mp4"
    good_file3.write_text("z")
    good_file4 = tmp_path / "good4.mp4"
    good_file4.write_text("w")
    missing = str(tmp_path / "gone.mp4")

    past_due = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    recent = (datetime.now(UTC) - timedelta(hours=2)).isoformat()

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    # 4 good + 2 broken = 33% broken. Trips rate_gate (>=25%). So we
    # actually still hit SAFETY GATE 1. Bump good count to 8 so rate_gate
    # opens (2/10 = 20%, under 25%).
    mock_cur.fetchall.return_value = [
        ("good-a", "ok", f'["{good_file}"]', None),
        ("good-b", "ok", f'["{good_file2}"]', None),
        ("good-c", "ok", f'["{good_file3}"]', None),
        ("good-d", "ok", f'["{good_file4}"]', None),
        ("good-e", "ok", f'["{good_file}"]', None),
        ("good-f", "ok", f'["{good_file2}"]', None),
        ("good-g", "ok", f'["{good_file3}"]', None),
        ("good-h", "ok", f'["{good_file4}"]', None),
        ("past-due-bp", "stuck", f'["{missing}"]', past_due),
        ("recent-sched-bp", "recent", f'["{missing}"]', recent),
    ]

    with patch.dict(
        os.environ,
        {"DATABASE_URL": "postgresql://fake", "GENLAB_PROJECT_ROOT": str(tmp_path)},
    ):
        with patch("psycopg.connect", return_value=mock_conn):
            alerts = check_missing_media("gaming")

    checks = {a.check: a for a in alerts}

    # Past-due went to WARNING lane
    assert "missing_media_past_due" in checks, (
        f"expected missing_media_past_due, got: {list(checks)}"
    )
    assert checks["missing_media_past_due"].severity == "warning"
    assert checks["missing_media_past_due"].details["blueprint_ids"] == ["past-due-bp"]

    # Recent scheduled stayed in CRITICAL sacred lane
    assert "missing_media_scheduled" in checks
    assert checks["missing_media_scheduled"].severity == "critical"
    assert checks["missing_media_scheduled"].details["blueprint_ids"] == ["recent-sched-bp"]

    # No auto-archive — R-79 still applies to both buckets.
    update_calls = [
        c
        for c in mock_cur.execute.call_args_list
        if "UPDATE blueprints SET status = 'ARCHIVED'" in c.args[0]
    ]
    assert not update_calls
