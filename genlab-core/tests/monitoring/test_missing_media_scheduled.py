"""R-79 regression: check_missing_media must never auto-archive a scheduled post.

cleanup_safety.md: "NEVER demote, delete, or clear data on blueprints that have
a scheduled_for date." The missing-media auto-fix previously selected
VISUAL_READY rows with no scheduled_for filter and archived them via raw SQL,
bypassing ScheduleGuardedProxy — so a transient mount/symlink miss could archive
a scheduled post. The fix archives only unscheduled broken blueprints and
surfaces scheduled-broken ones as an alert instead.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch


def test_missing_media_never_archives_scheduled_posts(tmp_path) -> None:
    from genlab_core.monitoring.health_monitor import check_missing_media

    # SAFETY GATE 2 requires GENLAB_PROJECT_ROOT/.tmp to exist.
    (tmp_path / ".tmp").mkdir()
    good_file = tmp_path / "good.mp4"
    good_file.write_text("x")
    missing = str(tmp_path / "gone.mp4")

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    # (id, title, visual_paths_json, scheduled_for)
    mock_cur.fetchall.return_value = [
        ("good-bp", "ok", f'["{good_file}"]', None),  # file exists -> not broken
        ("unsched-bp", "broken", f'["{missing}"]', None),  # broken + unscheduled -> archive
        ("sched-bp", "broken", f'["{missing}"]', "2026-06-01T06:30:00+00:00"),  # broken + scheduled -> protect
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
