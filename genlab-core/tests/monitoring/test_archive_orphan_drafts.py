"""R-81: ``archive_orphan_drafts`` now handles two flavours of orphan.

The audit (R-81 LOW) found that DRAFTED-with-video rows accumulate
slowly after R-47 (which made validation-failed renders stay DRAFTED
instead of incorrectly going VISUAL_READY). The original cleanup only
archived no-video drafts at 7d; this widening adds a second branch for
failed-video drafts at 14d.

Both branches preserve the non-negotiable ``cleanup_safety.md`` rule:
**never touch a row with `scheduled_for` set**, regardless of value.

These tests inspect the SQL strings the cursor sees + alert shape,
without needing a live Postgres. The storage integration coverage
lives separately in ``tests/storage/``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from genlab_core.monitoring.health_monitor import archive_orphan_drafts


def _mock_pg(no_video_count: int = 0, failed_video_count: int = 0):
    """Build a psycopg.connect mock with cursor that returns the
    configured RETURNING-id counts for the two consecutive UPDATEs."""
    cur = MagicMock()
    # First fetchall = no-video branch, second = failed-video branch
    cur.fetchall.side_effect = [
        [(f"no-video-{i}",) for i in range(no_video_count)],
        [(f"failed-video-{i}",) for i in range(failed_video_count)],
    ]
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


# ── SQL-shape pins ─────────────────────────────────────────────────────


def test_r81_runs_two_separate_updates_per_call() -> None:
    """One UPDATE per orphan flavour — keeps the two retention policies
    inspectable / tunable independently (7d vs 14d)."""
    conn, cur = _mock_pg()
    with patch("psycopg.connect", return_value=conn):
        archive_orphan_drafts("gaming")
    assert cur.execute.call_count == 2


def test_r81_no_video_branch_targets_empty_video_id_at_7_days() -> None:
    """Branch 1 must keep the original 7-day, no-video predicate."""
    conn, cur = _mock_pg()
    with patch("psycopg.connect", return_value=conn):
        archive_orphan_drafts("gaming")
    sql1 = cur.execute.call_args_list[0].args[0]
    assert "video_id IS NULL OR video_id = ''" in sql1
    assert "INTERVAL '7 days'" in sql1
    assert "auto_archived_orphan" in sql1


def test_r81_failed_video_branch_targets_present_video_id_at_14_days() -> None:
    """Branch 2 (R-81 widening) — failed-video drafts at the stricter
    14-day age."""
    conn, cur = _mock_pg()
    with patch("psycopg.connect", return_value=conn):
        archive_orphan_drafts("gaming")
    sql2 = cur.execute.call_args_list[1].args[0]
    assert "video_id IS NOT NULL" in sql2 and "video_id != ''" in sql2
    assert "INTERVAL '14 days'" in sql2
    assert "auto_archived_failed_video" in sql2


def test_r81_both_branches_preserve_scheduled_for_safety() -> None:
    """The ``cleanup_safety.md`` rule: never touch a row with
    ``scheduled_for`` set. BOTH UPDATEs must include the
    ``scheduled_for IS NULL`` predicate."""
    conn, cur = _mock_pg()
    with patch("psycopg.connect", return_value=conn):
        archive_orphan_drafts("gaming")
    for call in cur.execute.call_args_list:
        sql = call.args[0]
        assert "scheduled_for IS NULL" in sql, (
            f"R-81 SAFETY REGRESSION: branch missing scheduled_for guard.\nSQL: {sql}"
        )


def test_r81_both_branches_filter_by_niche_id() -> None:
    """Cross-channel isolation — every UPDATE is scoped by niche."""
    conn, cur = _mock_pg()
    with patch("psycopg.connect", return_value=conn):
        archive_orphan_drafts("anime")
    for call in cur.execute.call_args_list:
        sql, params = call.args
        assert "niche_id = %s" in sql
        assert params == ("anime",)


# ── Alert shape pins ───────────────────────────────────────────────────


def test_r81_no_alerts_when_nothing_archived() -> None:
    """A no-op run produces zero alerts — the daily health report
    shouldn't be cluttered with `0 archived` chatter."""
    conn, _ = _mock_pg(no_video_count=0, failed_video_count=0)
    with patch("psycopg.connect", return_value=conn):
        alerts = archive_orphan_drafts("gaming")
    assert alerts == []


def test_r81_only_no_video_branch_archives() -> None:
    """When only branch 1 archived rows, only one alert fires."""
    conn, _ = _mock_pg(no_video_count=3, failed_video_count=0)
    with patch("psycopg.connect", return_value=conn):
        alerts = archive_orphan_drafts("gaming")
    assert len(alerts) == 1
    assert alerts[0].check == "orphan_drafts_archived"
    assert alerts[0].details == {"count": 3}


def test_r81_only_failed_video_branch_archives() -> None:
    """When only branch 2 archived rows, only one alert fires — under
    the new ``failed_video_drafts_archived`` check name so operators can
    grep / threshold the two cases independently."""
    conn, _ = _mock_pg(no_video_count=0, failed_video_count=2)
    with patch("psycopg.connect", return_value=conn):
        alerts = archive_orphan_drafts("gaming")
    assert len(alerts) == 1
    assert alerts[0].check == "failed_video_drafts_archived"
    assert alerts[0].details == {"count": 2}


def test_r81_both_branches_archive_produces_two_alerts() -> None:
    """Both alerts fire and carry distinct check names so the daily
    health report shows the two populations separately."""
    conn, _ = _mock_pg(no_video_count=1, failed_video_count=4)
    with patch("psycopg.connect", return_value=conn):
        alerts = archive_orphan_drafts("gaming")
    assert len(alerts) == 2
    check_names = {a.check for a in alerts}
    assert check_names == {
        "orphan_drafts_archived",
        "failed_video_drafts_archived",
    }


# ── Failure mode pins ──────────────────────────────────────────────────


def test_r81_psycopg_failure_is_non_fatal() -> None:
    """A DB connection / query failure must not crash the health
    monitor — the docstring says "Returns a warning Alert" not
    "raises". The check is daily and best-effort."""
    with patch("psycopg.connect", side_effect=RuntimeError("conn refused")):
        alerts = archive_orphan_drafts("gaming")
    assert alerts == []
