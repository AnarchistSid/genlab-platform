"""R-81 (sub-item): ``archive_orphan_intake_stories`` cleans up stories
that have been done with — orphans + completed-rotation cases.

The audit's R-81 row called out "INTAKE stories have no cleanup path".
``stories.status`` never advances past INTAKE in live code
(``update_story_status`` exists in the store API but no live caller
exercises it — grep-verified), so this cleanup can't filter on status
alone; it has to inspect the blueprint-reference graph.

A story is safe to archive iff EITHER:

  * no blueprint references it (true orphan), OR
  * every referencing blueprint is in a terminal state (PUBLISHED or
    ARCHIVED) — rotation complete.

Non-terminal blueprint states (DRAFTED, VISUAL_READY, PUBLISHING,
PUBLISH_FAILED) protect the story; the ``cleanup_safety.md``
"scheduled posts are sacred" rule applies transitively because a
scheduled blueprint sits in VISUAL_READY (non-terminal).

These tests inspect the SQL strings + alert shape via psycopg mock,
matching the pattern of ``test_archive_orphan_drafts.py``. Storage
integration coverage lives separately in ``tests/storage/``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from genlab_core.monitoring.health_monitor import archive_orphan_intake_stories


def _mock_pg(archived_count: int = 0):
    cur = MagicMock()
    cur.fetchall.return_value = [(f"story-{i}",) for i in range(archived_count)]
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


# ── SQL-shape pins ────────────────────────────────────────────────────


def test_r81_intake_cleanup_runs_one_update_per_call() -> None:
    """Single UPDATE — the safety lives in the WHERE clause, not in
    multiple branches."""
    conn, cur = _mock_pg()
    with patch("psycopg.connect", return_value=conn):
        archive_orphan_intake_stories("gaming")
    assert cur.execute.call_count == 1


def test_r81_intake_cleanup_targets_status_intake_at_30_days() -> None:
    """The cleanup is scoped to status=INTAKE at the 30-day age floor.
    30d is the deliberate 2× of the no-video draft (7d) and >2× of
    the failed-video draft (14d) — stories live longer in the pipeline
    conceptually (multiple blueprints can be drawn from one story
    over time, so the cleanup window is more conservative)."""
    conn, cur = _mock_pg()
    with patch("psycopg.connect", return_value=conn):
        archive_orphan_intake_stories("gaming")
    sql = cur.execute.call_args.args[0]
    assert "UPDATE stories" in sql
    assert "status = 'INTAKE'" in sql
    assert "INTERVAL '30 days'" in sql


def test_r81_intake_cleanup_protects_stories_with_non_terminal_blueprints() -> None:
    """A story whose blueprint is still in DRAFTED / VISUAL_READY /
    PUBLISHING / PUBLISH_FAILED must NOT be archived. The check uses
    ``NOT IN ('PUBLISHED', 'ARCHIVED')`` — any non-terminal state
    keeps the story alive."""
    conn, cur = _mock_pg()
    with patch("psycopg.connect", return_value=conn):
        archive_orphan_intake_stories("gaming")
    sql = cur.execute.call_args.args[0]
    # NOT EXISTS subquery joins on story_id + niche_id
    assert "NOT EXISTS" in sql
    assert "blueprints.story_id = stories.story_id" in sql
    assert "blueprints.niche_id = stories.niche_id" in sql
    # And gates on a TERMINAL-only NOT-IN: only PUBLISHED + ARCHIVED
    # are safe-to-archive-the-parent triggers.
    assert "blueprints.status NOT IN ('PUBLISHED', 'ARCHIVED')" in sql


def test_r81_intake_cleanup_filters_by_niche_id() -> None:
    """Cross-channel isolation: the cleanup is scoped to the niche
    whose health is being checked."""
    conn, cur = _mock_pg()
    with patch("psycopg.connect", return_value=conn):
        archive_orphan_intake_stories("anime")
    sql, params = cur.execute.call_args.args
    assert "WHERE niche_id = %s" in sql
    assert params == ("anime",)


def test_r81_intake_cleanup_transitive_scheduled_safety_via_visual_ready() -> None:
    """The ``cleanup_safety.md`` "scheduled posts are sacred" rule
    applies transitively here: a scheduled blueprint sits in
    VISUAL_READY (a non-terminal status), and the WHERE clause's
    ``NOT IN ('PUBLISHED', 'ARCHIVED')`` predicate keeps such a
    blueprint's parent story alive.

    Pin: ``VISUAL_READY`` MUST NOT appear in the terminal set."""
    conn, cur = _mock_pg()
    with patch("psycopg.connect", return_value=conn):
        archive_orphan_intake_stories("gaming")
    sql = cur.execute.call_args.args[0]
    # The terminal set is exactly ('PUBLISHED', 'ARCHIVED').
    # VISUAL_READY in that set would silently break the scheduled-post
    # safety chain — a regression worth pinning explicitly.
    terminal_set_section = sql[sql.index("NOT IN ('") : sql.index(")", sql.index("NOT IN ('"))]
    assert "VISUAL_READY" not in terminal_set_section
    assert "DRAFTED" not in terminal_set_section
    assert "PUBLISHING" not in terminal_set_section
    assert "PUBLISH_FAILED" not in terminal_set_section


# ── Alert shape pins ──────────────────────────────────────────────────


def test_r81_intake_no_alert_when_nothing_archived() -> None:
    """No-op runs produce no alerts."""
    conn, _ = _mock_pg(archived_count=0)
    with patch("psycopg.connect", return_value=conn):
        alerts = archive_orphan_intake_stories("gaming")
    assert alerts == []


def test_r81_intake_alert_carries_distinct_check_name() -> None:
    """The check name is ``orphan_intake_stories_archived`` —
    distinct from the DRAFTED-cleanup ones so operators can grep
    each population independently in the daily health report."""
    conn, _ = _mock_pg(archived_count=5)
    with patch("psycopg.connect", return_value=conn):
        alerts = archive_orphan_intake_stories("gaming")
    assert len(alerts) == 1
    assert alerts[0].check == "orphan_intake_stories_archived"
    assert alerts[0].details == {"count": 5}
    assert alerts[0].severity == "warning"


# ── Failure-mode pin ──────────────────────────────────────────────────


def test_r81_intake_psycopg_failure_is_non_fatal() -> None:
    """The daily health check is best-effort; a connection failure
    must not crash the monitor."""
    with patch("psycopg.connect", side_effect=RuntimeError("conn refused")):
        alerts = archive_orphan_intake_stories("gaming")
    assert alerts == []
