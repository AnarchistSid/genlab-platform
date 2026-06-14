"""``archive_orphan_drafts`` handles three flavours of orphan.

R-81 (LOW) widened the original single-branch cleanup to add Branch 2
(failed-video drafts at 14d). The 2026-06-14 deploy-pipeline-gap
investigation widened it again to add Branch 2a (render-never-completed
drafts at 7d) — when a 14-day window let 94 youtube_trending DRAFTED
rows accumulate during the WARP outage period because they had video_id
(escaping Branch 1) but never had ``visual_paths`` populated (escaping
the design intent of Branch 2 which was for validation-failed renders).

All three branches preserve the non-negotiable ``cleanup_safety.md``
rule: **never touch a row with `scheduled_for` set**, regardless of
value.

These tests inspect the SQL strings the cursor sees + alert shape,
without needing a live Postgres. The storage integration coverage
lives separately in ``tests/storage/``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from genlab_core.monitoring.health_monitor import archive_orphan_drafts


def _mock_pg(
    no_video_count: int = 0,
    render_never_count: int = 0,
    failed_video_count: int = 0,
):
    """Build a psycopg.connect mock with cursor that returns the
    configured RETURNING-id counts for the three consecutive UPDATEs.

    SQL execution order (must stay in sync with health_monitor.py):
        1. Branch 1  — no-video (7d)
        2. Branch 2a — render-never-completed (7d) [added 2026-06-14]
        3. Branch 2  — failed-video / validation-failed (14d)
    """
    cur = MagicMock()
    cur.fetchall.side_effect = [
        [(f"no-video-{i}",) for i in range(no_video_count)],
        [(f"render-never-{i}",) for i in range(render_never_count)],
        [(f"failed-video-{i}",) for i in range(failed_video_count)],
    ]
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


# ── SQL-shape pins ─────────────────────────────────────────────────────


def test_runs_three_separate_updates_per_call() -> None:
    """One UPDATE per orphan flavour — keeps the three retention
    policies (7d no-video, 7d render-never-completed, 14d failed-video)
    inspectable / tunable independently."""
    conn, cur = _mock_pg()
    with patch("psycopg.connect", return_value=conn):
        archive_orphan_drafts("gaming")
    assert cur.execute.call_count == 3


def test_r81_no_video_branch_targets_empty_video_id_at_7_days() -> None:
    """Branch 1 must keep the original 7-day, no-video predicate."""
    conn, cur = _mock_pg()
    with patch("psycopg.connect", return_value=conn):
        archive_orphan_drafts("gaming")
    sql1 = cur.execute.call_args_list[0].args[0]
    assert "video_id IS NULL OR video_id = ''" in sql1
    assert "INTERVAL '7 days'" in sql1
    assert "auto_archived_orphan" in sql1


def test_render_never_completed_branch_targets_present_video_id_without_visual_paths_at_7_days() -> (
    None
):
    """Branch 2a (2026-06-14): drafts where a video was identified but
    the render binary never produced an output. 7d cutoff because
    YouTube trending churn means the video won't be re-fetched anyway."""
    conn, cur = _mock_pg()
    with patch("psycopg.connect", return_value=conn):
        archive_orphan_drafts("gaming")
    sql_2a = cur.execute.call_args_list[1].args[0]
    assert "video_id IS NOT NULL" in sql_2a and "video_id != ''" in sql_2a
    assert "INTERVAL '7 days'" in sql_2a
    assert "extra->>'visual_paths' IS NULL" in sql_2a
    assert "auto_archived_render_never_completed" in sql_2a


def test_failed_video_branch_targets_present_video_id_at_14_days() -> None:
    """Branch 2 (R-81 widening) — validation-failed drafts at the stricter
    14-day age. After Branch 2a's narrower 7d filter for render-never-
    completed rows, Branch 2 catches the residual where render DID
    produce output but validation rejected it."""
    conn, cur = _mock_pg()
    with patch("psycopg.connect", return_value=conn):
        archive_orphan_drafts("gaming")
    sql2 = cur.execute.call_args_list[2].args[0]
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


def test_only_render_never_completed_branch_archives() -> None:
    """Branch 2a in isolation fires its own alert with a distinct
    check name so the dashboard can chart this failure mode separately
    from validation-failure (Branch 2) or no-video (Branch 1)."""
    conn, _ = _mock_pg(no_video_count=0, render_never_count=5, failed_video_count=0)
    with patch("psycopg.connect", return_value=conn):
        alerts = archive_orphan_drafts("gaming")
    assert len(alerts) == 1
    assert alerts[0].check == "render_never_completed_drafts_archived"
    assert alerts[0].details == {"count": 5}


def test_only_failed_video_branch_archives() -> None:
    """When only Branch 2 archived rows, only one alert fires — under
    the ``failed_video_drafts_archived`` check name so operators can
    grep / threshold the cases independently."""
    conn, _ = _mock_pg(no_video_count=0, render_never_count=0, failed_video_count=2)
    with patch("psycopg.connect", return_value=conn):
        alerts = archive_orphan_drafts("gaming")
    assert len(alerts) == 1
    assert alerts[0].check == "failed_video_drafts_archived"
    assert alerts[0].details == {"count": 2}


def test_all_three_branches_archive_produces_three_alerts() -> None:
    """All three alerts fire with distinct check names so the daily
    health report can show the three populations separately."""
    conn, _ = _mock_pg(no_video_count=1, render_never_count=3, failed_video_count=4)
    with patch("psycopg.connect", return_value=conn):
        alerts = archive_orphan_drafts("gaming")
    assert len(alerts) == 3
    check_names = {a.check for a in alerts}
    assert check_names == {
        "orphan_drafts_archived",
        "render_never_completed_drafts_archived",
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
