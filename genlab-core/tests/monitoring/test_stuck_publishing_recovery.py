"""Regression tests for check_stuck_publishing in health_monitor."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch


def test_check_stuck_publishing_handles_missing_db_url_gracefully() -> None:
    from genlab_core.monitoring.health_monitor import check_stuck_publishing

    old_url = os.environ.pop("DATABASE_URL", None)
    try:
        alerts = check_stuck_publishing("anime")
        assert isinstance(alerts, list)
    finally:
        if old_url is not None:
            os.environ["DATABASE_URL"] = old_url


def test_check_stuck_publishing_builds_correct_recovery_alerts() -> None:
    from genlab_core.monitoring.health_monitor import check_stuck_publishing

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    mock_cur.fetchall.return_value = [
        ("bp-uuid-1", "Stuck Row", "2026-04-02 06:01:37", 0, "{}"),
    ]

    with patch.dict(os.environ, {"DATABASE_URL": "postgresql://fake"}):
        with patch("psycopg.connect", return_value=mock_conn):
            alerts = check_stuck_publishing("anime")

    assert len(alerts) == 1
    assert alerts[0].check == "stuck_publishing"
    assert alerts[0].auto_fix


def test_stuck_publishing_recovers_partial_success_as_published() -> None:
    from genlab_core.monitoring.health_monitor import check_stuck_publishing

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    mock_cur.fetchall.return_value = [
        (
            "bp-uuid-2",
            "Partial Success",
            "2026-04-02 06:01:37",
            1,
            '{"instagram": "PUBLISHED", "youtube": "PENDING"}',
        ),
    ]

    with patch.dict(os.environ, {"DATABASE_URL": "postgresql://fake"}):
        with patch("psycopg.connect", return_value=mock_conn):
            alerts = check_stuck_publishing("anime")

    assert len(alerts) == 1
    assert alerts[0].auto_fix == "status=PUBLISHED"


def test_stuck_publishing_marks_failed_after_3_attempts() -> None:
    from genlab_core.monitoring.health_monitor import check_stuck_publishing

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    mock_cur.fetchall.return_value = [
        ("bp-uuid-3", "Failed Too Often", "2026-04-02 06:01:37", 3, "{}"),
    ]

    with patch.dict(os.environ, {"DATABASE_URL": "postgresql://fake"}):
        with patch("psycopg.connect", return_value=mock_conn):
            alerts = check_stuck_publishing("anime")

    assert len(alerts) == 1
    assert alerts[0].auto_fix == "status=PUBLISH_FAILED"
