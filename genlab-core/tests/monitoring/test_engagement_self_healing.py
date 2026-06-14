"""2026-06-14 engagement self-healing trio — PR #199.

Pins for the three additions that target the engagement-loop audit's
three compounding failures:

  Fix C: Pre-flight task-count assertion in the poller — surface
         silently-skipped (niche × platform) pollers.
  Fix D: ``archive_stranded_engagement_reviews`` — auto-archive
         ``pending_engagement`` rows in ``pending_review`` for >7d.
  Fix E: ``detect_dead_pollers`` — escalation alert when token_expired
         has been unresolved for >7d.

Each fix is the SAME architectural pattern: surface the dead state so
the operator sees it within one daily health-monitor tick instead of
24 days from now.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch


def _mock_pg(rows: list = None):
    """psycopg.connect mock with cursor that returns the given rows."""
    cur = MagicMock()
    cur.fetchall.return_value = rows or []
    cur.__enter__ = lambda self: self
    cur.__exit__ = lambda self, *a: None
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.__enter__ = lambda self: self
    conn.__exit__ = lambda self, *a: None
    return conn, cur


# ─────────────────────────────────────────────────────────────────────────
# Fix D: archive_stranded_engagement_reviews
# ─────────────────────────────────────────────────────────────────────────


def test_archive_stranded_reviews_targets_pending_review_at_7d():
    """SQL must filter to status='pending_review' + created_at older
    than 7 days. The 7d window matches Branch 1/2a of archive_orphan_drafts
    so the engagement queue gets the same patience semantics."""
    from genlab_core.monitoring.health_monitor import archive_stranded_engagement_reviews

    conn, cur = _mock_pg(rows=[("uuid1",), ("uuid2",), ("uuid3",)])
    with patch("psycopg.connect", return_value=conn):
        alerts = archive_stranded_engagement_reviews("sports")

    sql = cur.execute.call_args.args[0]
    assert "pending_engagement" in sql
    assert "status = 'pending_review'" in sql
    assert "INTERVAL '7 days'" in sql
    assert "auto_archived_stranded" in sql
    # Alert shape — one alert per archive batch with count
    assert len(alerts) == 1
    assert alerts[0].check == "stranded_engagement_reviews_archived"
    assert alerts[0].details["count"] == 3


def test_archive_stranded_no_alert_when_nothing_archived():
    """Daily health report shouldn't be cluttered with "0 archived"
    chatter — matches the pattern from PR #190's Branch 2a."""
    from genlab_core.monitoring.health_monitor import archive_stranded_engagement_reviews

    conn, _ = _mock_pg(rows=[])
    with patch("psycopg.connect", return_value=conn):
        alerts = archive_stranded_engagement_reviews("sports")
    assert alerts == []


def test_archive_stranded_db_failure_is_non_fatal():
    """Same pattern as archive_orphan_drafts — never crash the daily
    health-monitor tick on a DB hiccup."""
    from genlab_core.monitoring.health_monitor import archive_stranded_engagement_reviews

    with patch("psycopg.connect", side_effect=RuntimeError("conn refused")):
        alerts = archive_stranded_engagement_reviews("sports")
    assert alerts == []


def test_archive_stranded_scoped_by_niche_id():
    """Cross-niche isolation: the UPDATE must filter by niche_id so
    a sports cleanup never touches gaming."""
    from genlab_core.monitoring.health_monitor import archive_stranded_engagement_reviews

    conn, cur = _mock_pg(rows=[])
    with patch("psycopg.connect", return_value=conn):
        archive_stranded_engagement_reviews("anime")

    sql, params = cur.execute.call_args.args
    assert "niche_id = %s" in sql
    assert params == ("anime",)


# ─────────────────────────────────────────────────────────────────────────
# Fix E: detect_dead_pollers
# ─────────────────────────────────────────────────────────────────────────


def test_detect_dead_pollers_emits_critical_when_token_alerts_old():
    """When a niche × platform has had unresolved token_expired alerts
    for >7 days, emit a critical 'dead_poller' alert separate from the
    repeating 'token_expired' one — gives the operator a distinct
    'this has been broken for a week' signal."""
    from genlab_core.monitoring.health_monitor import detect_dead_pollers

    first_seen = datetime.now(UTC) - timedelta(days=14)
    conn, _ = _mock_pg(rows=[("threads", first_seen, 720)])
    with patch("psycopg.connect", return_value=conn):
        alerts = detect_dead_pollers("sports")

    assert len(alerts) == 1
    assert alerts[0].check == "dead_poller"
    assert alerts[0].severity == "critical"
    assert alerts[0].details["platform"] == "threads"
    assert alerts[0].details["days_stuck"] == 14
    assert alerts[0].details["unresolved_token_alerts"] == 720
    # Message must reference PR #198 (the actual fix) so the operator
    # has a pointer to the auto-refresh mechanism.
    assert "PR #198" in alerts[0].message


def test_detect_dead_pollers_no_alert_when_token_alerts_fresh():
    """token_expired alerts <7 days old don't trigger dead_poller —
    the repeating token_expired itself is enough signal in that window;
    the dead_poller escalation only fires after the repeating noise
    has been there long enough that the operator clearly missed it."""
    from genlab_core.monitoring.health_monitor import detect_dead_pollers

    conn, _ = _mock_pg(rows=[])  # SQL filters out <7d rows; mock returns []
    with patch("psycopg.connect", return_value=conn):
        alerts = detect_dead_pollers("sports")
    assert alerts == []


def test_detect_dead_pollers_db_failure_is_non_fatal():
    """Visibility check must never break health-monitor."""
    from genlab_core.monitoring.health_monitor import detect_dead_pollers

    with patch("psycopg.connect", side_effect=RuntimeError("conn refused")):
        alerts = detect_dead_pollers("sports")
    assert alerts == []


# ─────────────────────────────────────────────────────────────────────────
# Fix C: pre-flight task-count assertion
# ─────────────────────────────────────────────────────────────────────────


def test_preflight_task_count_logs_one_warning_per_skipped_pair():
    """The audit found YT and Twitter pollers silently skipping 5 of 5
    niches each. After this fix, each skipped (niche × platform) pair
    must produce a single named warning with the env var to set."""
    from genlab_core.engagement.poller import assert_expected_task_count

    skipped = [
        ("sports", "youtube", "CLUTCHWIRE_YOUTUBE_CHANNEL_ID"),
        ("gaming", "youtube", "CRITICALRUSH_YOUTUBE_CHANNEL_ID"),
    ]
    with patch("genlab_core.engagement.poller.logger") as mock_logger:
        assert_expected_task_count(skipped)

    # 1 summary line + 2 per-pair lines = 3 warning calls
    assert mock_logger.warning.call_count == 3
    # Each per-pair call must include the env var name
    call_messages = [str(call.args) for call in mock_logger.warning.call_args_list]
    joined = " ".join(call_messages)
    assert "CLUTCHWIRE_YOUTUBE_CHANNEL_ID" in joined
    assert "CRITICALRUSH_YOUTUBE_CHANNEL_ID" in joined


def test_preflight_task_count_silent_when_nothing_skipped():
    """When all (niche × platform) pairs queued successfully, no
    warning fires — keeps the journalctl clean in the happy path."""
    from genlab_core.engagement.poller import assert_expected_task_count

    with patch("genlab_core.engagement.poller.logger") as mock_logger:
        assert_expected_task_count([])
    mock_logger.warning.assert_not_called()


def test_niche_env_prefix_covers_all_five_niches():
    """Sanity: every niche from CLAUDE.md must map to its env prefix.
    Without this, the SKIPPED warning would tell the operator to set
    a wrongly-prefixed env var."""
    from genlab_core.engagement.poller import niche_env_prefix

    expected = {
        "ai_creators": "BLACKBOXBRIEF",
        "gaming": "CRITICALRUSH",
        "sports": "CLUTCHWIRE",
        "movies": "SPLICEREEL",
        "anime": "FRAMEDRIFT",
    }
    for niche, prefix in expected.items():
        assert niche_env_prefix(niche) == prefix, (
            f"niche {niche} should map to {prefix}; got {niche_env_prefix(niche)}"
        )
