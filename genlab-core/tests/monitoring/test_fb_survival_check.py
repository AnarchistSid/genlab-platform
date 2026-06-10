"""Tests for :mod:`genlab_core.monitoring.fb_survival_check`.

Audit ref: R-33.

Mocks psycopg.connect + FacebookClient — no live DB or Graph API.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from genlab_core.monitoring import fb_survival_check

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_conn(fetchall_rows=None):
    """Build a psycopg connection mock with a controllable cursor."""
    cursor = MagicMock()
    cursor.fetchall.return_value = fetchall_rows or []
    cursor.__enter__.return_value = cursor
    cursor.__exit__.return_value = False

    conn = MagicMock()
    conn.cursor.return_value = cursor
    # Direct conn.execute (used by mark_checked / mark_removed) returns ok.
    conn.execute.return_value = MagicMock()
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    return conn, cursor


# ---------------------------------------------------------------------------
# find_candidates — SELECT shape
# ---------------------------------------------------------------------------


class TestFindCandidates:
    def test_returns_rows_from_cursor(self) -> None:
        rows = [
            {"id": "uuid1", "post_id": "fb_post_1", "niche_id": "gaming"},
            {"id": "uuid2", "post_id": "fb_post_2", "niche_id": "anime"},
        ]
        conn, _ = _make_mock_conn(fetchall_rows=rows)
        with patch("psycopg.connect", return_value=conn):
            result = fb_survival_check.find_candidates()
        assert result == rows

    def test_query_filters_to_facebook_and_live_statuses(self) -> None:
        conn, cursor = _make_mock_conn()
        with patch("psycopg.connect", return_value=conn):
            fb_survival_check.find_candidates()
        sql, params = cursor.execute.call_args[0]
        assert "platform = 'facebook'" in sql
        assert "status   = ANY(%s)" in sql
        assert "post_id IS NOT NULL" in sql
        # First positional param: the live-post status list. Must cover
        # the full SUCCESS → INSIGHTS_* progression so a post can still
        # be survival-checked once metric_collector promotes its status.
        assert params[0] == list(fb_survival_check.LIVE_POST_STATUSES)
        for required in ("SUCCESS", "INSIGHTS_6H", "INSIGHTS_48H", "INSIGHTS_168H"):
            assert required in params[0]

    def test_query_uses_provided_age_window(self) -> None:
        conn, cursor = _make_mock_conn()
        with patch("psycopg.connect", return_value=conn):
            fb_survival_check.find_candidates(min_age_hours=12, max_age_hours=48)
        sql = cursor.execute.call_args[0][0]
        assert "12 hours" in sql
        assert "48 hours" in sql

    def test_query_excludes_already_checked_rows(self) -> None:
        conn, cursor = _make_mock_conn()
        with patch("psycopg.connect", return_value=conn):
            fb_survival_check.find_candidates()
        sql = cursor.execute.call_args[0][0]
        # The extra->>'fb_survival_checked' IS NULL filter is the
        # idempotency hinge — without it the daily run would re-check
        # rows it already saw.
        assert "(extra->>%s) IS NULL" in sql
        params = cursor.execute.call_args[0][1]
        # Params order: (status_list, EXTRA_KEY_CHECKED, limit).
        assert params[1] == fb_survival_check.EXTRA_KEY_CHECKED


# ---------------------------------------------------------------------------
# mark_checked / mark_removed — UPDATE shape
# ---------------------------------------------------------------------------


class TestMarkChecked:
    def test_stamps_extra_key_checked(self) -> None:
        conn, _ = _make_mock_conn()
        with patch("psycopg.connect", return_value=conn):
            fb_survival_check.mark_checked(row_id="uuid_a")
        sql, params = conn.execute.call_args[0]
        assert "UPDATE publishing_analytics" in sql
        assert "jsonb_build_object(%s" in sql
        # First param is the key, second is the timestamp, third is row id.
        assert params[0] == fb_survival_check.EXTRA_KEY_CHECKED
        assert params[2] == "uuid_a"

    def test_doesnt_change_status(self) -> None:
        conn, _ = _make_mock_conn()
        with patch("psycopg.connect", return_value=conn):
            fb_survival_check.mark_checked(row_id="uuid_a")
        sql = conn.execute.call_args[0][0]
        # mark_checked must NEVER touch the status column.
        assert "SET status" not in sql
        assert "REMOVED_BY_META" not in sql


class TestMarkRemoved:
    def test_flips_status_to_removed_by_meta(self) -> None:
        conn, _ = _make_mock_conn()
        with patch("psycopg.connect", return_value=conn):
            fb_survival_check.mark_removed(row_id="uuid_b")
        sql, params = conn.execute.call_args[0]
        assert "UPDATE publishing_analytics" in sql
        assert "SET status = %s" in sql
        assert params[0] == "REMOVED_BY_META"

    def test_stamps_both_checked_and_removed_timestamps(self) -> None:
        conn, _ = _make_mock_conn()
        with patch("psycopg.connect", return_value=conn):
            fb_survival_check.mark_removed(row_id="uuid_b")
        params = conn.execute.call_args[0][1]
        # Order from the SQL: (status, checked_key, ts1, removed_key, ts2, row_id)
        assert params[1] == fb_survival_check.EXTRA_KEY_CHECKED
        assert params[3] == fb_survival_check.EXTRA_KEY_REMOVED
        assert params[2] == params[4]  # both stamps in the same UPDATE = same ts
        assert params[5] == "uuid_b"


# ---------------------------------------------------------------------------
# run_check — the orchestration
# ---------------------------------------------------------------------------


def _fake_client(side_effects):
    """Build a fake FB client whose check_post_alive yields ``side_effects``."""
    iter_ = iter(side_effects)
    client = MagicMock()
    client.check_post_alive.side_effect = lambda post_id: next(iter_)
    return client


class TestRunCheck:
    def test_no_candidates_returns_zero_counts(self) -> None:
        conn, _ = _make_mock_conn()
        with patch("psycopg.connect", return_value=conn):
            result = fb_survival_check.run_check(client=_fake_client([]))
        assert result == {
            "examined": 0,
            "alive": 0,
            "removed": 0,
            "ambiguous": 0,
            "error": 0,
        }

    def test_counts_alive_removed_ambiguous_separately(self) -> None:
        rows = [
            {"id": "u1", "post_id": "p1", "niche_id": "gaming", "published_at": "x"},
            {"id": "u2", "post_id": "p2", "niche_id": "anime", "published_at": "x"},
            {"id": "u3", "post_id": "p3", "niche_id": "movies", "published_at": "x"},
        ]
        with (
            patch.object(fb_survival_check, "find_candidates", return_value=rows),
            patch.object(fb_survival_check, "mark_checked"),
            patch.object(fb_survival_check, "mark_removed"),
        ):
            result = fb_survival_check.run_check(
                client=_fake_client([True, False, None]),
            )
        assert result["examined"] == 3
        assert result["alive"] == 1
        assert result["removed"] == 1
        assert result["ambiguous"] == 1
        assert result["error"] == 0

    def test_alive_calls_mark_checked(self) -> None:
        rows = [{"id": "u1", "post_id": "p1", "niche_id": "g", "published_at": "x"}]
        with (
            patch.object(fb_survival_check, "find_candidates", return_value=rows),
            patch.object(fb_survival_check, "mark_checked") as m_ok,
            patch.object(fb_survival_check, "mark_removed") as m_bad,
        ):
            fb_survival_check.run_check(client=_fake_client([True]))
        m_ok.assert_called_once_with(row_id="u1", dsn=None)
        m_bad.assert_not_called()

    def test_removed_calls_mark_removed(self) -> None:
        rows = [{"id": "u1", "post_id": "p1", "niche_id": "g", "published_at": "x"}]
        with (
            patch.object(fb_survival_check, "find_candidates", return_value=rows),
            patch.object(fb_survival_check, "mark_checked") as m_ok,
            patch.object(fb_survival_check, "mark_removed") as m_bad,
        ):
            fb_survival_check.run_check(client=_fake_client([False]))
        m_bad.assert_called_once_with(row_id="u1", dsn=None)
        m_ok.assert_not_called()

    def test_ambiguous_calls_neither(self) -> None:
        rows = [{"id": "u1", "post_id": "p1", "niche_id": "g", "published_at": "x"}]
        with (
            patch.object(fb_survival_check, "find_candidates", return_value=rows),
            patch.object(fb_survival_check, "mark_checked") as m_ok,
            patch.object(fb_survival_check, "mark_removed") as m_bad,
        ):
            fb_survival_check.run_check(client=_fake_client([None]))
        m_ok.assert_not_called()
        m_bad.assert_not_called()

    def test_client_exception_counted_as_error_doesnt_stop_loop(self) -> None:
        rows = [
            {"id": "u1", "post_id": "p1", "niche_id": "g", "published_at": "x"},
            {"id": "u2", "post_id": "p2", "niche_id": "g", "published_at": "x"},
        ]
        client = MagicMock()
        client.check_post_alive.side_effect = [RuntimeError("crash"), True]
        with (
            patch.object(fb_survival_check, "find_candidates", return_value=rows),
            patch.object(fb_survival_check, "mark_checked") as m_ok,
        ):
            result = fb_survival_check.run_check(client=client)
        assert result["error"] == 1
        assert result["alive"] == 1  # second row still processed
        m_ok.assert_called_once()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCli:
    def test_main_passes_args_through(self) -> None:
        with patch.object(fb_survival_check, "run_check") as mock_run:
            mock_run.return_value = {
                "examined": 0,
                "alive": 0,
                "removed": 0,
                "ambiguous": 0,
                "error": 0,
            }
            exit_code = fb_survival_check.main(
                ["--min-age-hours", "12", "--max-age-hours", "72", "--limit", "50"]
            )
        assert exit_code == 0
        kwargs = mock_run.call_args.kwargs
        assert kwargs["min_age_hours"] == 12
        assert kwargs["max_age_hours"] == 72
        assert kwargs["limit"] == 50

    def test_main_default_args(self) -> None:
        with patch.object(fb_survival_check, "run_check") as mock_run:
            mock_run.return_value = {
                "examined": 0,
                "alive": 0,
                "removed": 0,
                "ambiguous": 0,
                "error": 0,
            }
            fb_survival_check.main([])
        kwargs = mock_run.call_args.kwargs
        assert kwargs["min_age_hours"] == 24
        assert kwargs["max_age_hours"] == 168
        assert kwargs["limit"] == 200
