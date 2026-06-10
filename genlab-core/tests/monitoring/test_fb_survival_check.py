"""Tests for :mod:`genlab_core.monitoring.fb_survival_check`.

Audit ref: R-33.

Mocks psycopg.connect + FacebookClient — no live DB or Graph API.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
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

    def test_every_jsonb_build_object_arg_has_text_cast(self) -> None:
        # psycopg3 raises IndeterminateDatatype if any
        # jsonb_build_object argument lacks an explicit ::text cast
        # (the function is variadic so type inference can't help).
        # Tests run against mocks so this rule has to be enforced
        # syntactically — a static SQL audit.
        conn, _ = _make_mock_conn()
        with patch("psycopg.connect", return_value=conn):
            fb_survival_check.mark_checked(row_id="uuid_a")
        sql = conn.execute.call_args[0][0]
        # Extract the jsonb_build_object(...) call body and verify
        # every %s placeholder inside it has ::text suffix.
        import re

        match = re.search(r"jsonb_build_object\(([^)]+)\)", sql, re.DOTALL)
        assert match, "expected a jsonb_build_object call in the SQL"
        body = match.group(1)
        placeholders = re.findall(r"%s(?:::\w+)?", body)
        for p in placeholders:
            assert p.endswith("::text"), f"unsafe placeholder {p!r} in {body}"


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

    def test_every_jsonb_build_object_arg_has_text_cast(self) -> None:
        # Same guarantee as in TestMarkChecked — psycopg3 needs every
        # variadic jsonb_build_object arg to carry an explicit cast.
        conn, _ = _make_mock_conn()
        with patch("psycopg.connect", return_value=conn):
            fb_survival_check.mark_removed(row_id="uuid_b")
        sql = conn.execute.call_args[0][0]
        import re

        match = re.search(r"jsonb_build_object\(([^)]+)\)", sql, re.DOTALL)
        assert match
        body = match.group(1)
        placeholders = re.findall(r"%s(?:::\w+)?", body)
        for p in placeholders:
            assert p.endswith("::text"), f"unsafe placeholder {p!r} in {body}"


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
# run_check — sanity rate guard (post-incident 2026-06-10)
# ---------------------------------------------------------------------------


class TestRemovalRateGuard:
    def _rows(self, n: int) -> list[dict]:
        return [
            {
                "id": f"u{i}",
                "post_id": f"p{i}",
                "niche_id": "gaming",
                "published_at": "x",
            }
            for i in range(n)
        ]

    def test_raises_when_removal_rate_exceeds_threshold(self) -> None:
        # 12 rows all False → 100% removal rate, well above the
        # default 0.30 threshold (and above the
        # min_examined_for_rate_check=10 floor).
        rows = self._rows(12)
        client = MagicMock()
        client.check_post_alive.return_value = False
        with (
            patch.object(fb_survival_check, "find_candidates", return_value=rows),
            patch.object(fb_survival_check, "mark_removed") as m_bad,
            patch.object(fb_survival_check, "mark_checked"),
        ):
            with pytest.raises(fb_survival_check.RemovalRateExceeded) as ei:
                fb_survival_check.run_check(client=client)
        # The guard intentionally allows the first `min_examined`
        # rows through unguarded (small samples aren't statistically
        # actionable). With the default floor of 10, the first 9
        # removals are written before the 10th-row check trips.
        # That's the deliberate trade-off: we accept up to floor-1
        # false-positives as the cost of not aborting on a single
        # real removal in a small batch.
        assert "removal rate" in str(ei.value) or "ABORT" in str(ei.value).upper()
        # Cap on writes: never more than min_examined_for_rate_check - 1.
        assert m_bad.call_count <= 9
        # And we definitely refused to write the rest of the 12 rows.
        assert m_bad.call_count < 12

    def test_no_abort_below_threshold(self) -> None:
        # 1 removed out of 10 = 10%; below the 30% guard.
        rows = self._rows(10)
        client = MagicMock()
        # Pattern: 9 alive, 1 removed at the end. Rate = 10%.
        client.check_post_alive.side_effect = [True] * 9 + [False]
        with (
            patch.object(fb_survival_check, "find_candidates", return_value=rows),
            patch.object(fb_survival_check, "mark_removed") as m_bad,
            patch.object(fb_survival_check, "mark_checked"),
        ):
            result = fb_survival_check.run_check(client=client)
        assert result["removed"] == 1
        assert result["alive"] == 9
        m_bad.assert_called_once()

    def test_no_abort_below_min_examined_floor(self) -> None:
        # 3 examined, all removed (100%). Above the rate threshold
        # but BELOW min_examined_for_rate_check (default 10), so the
        # guard doesn't fire — small samples are not actionable.
        rows = self._rows(3)
        client = MagicMock()
        client.check_post_alive.return_value = False
        with (
            patch.object(fb_survival_check, "find_candidates", return_value=rows),
            patch.object(fb_survival_check, "mark_removed") as m_bad,
            patch.object(fb_survival_check, "mark_checked"),
        ):
            result = fb_survival_check.run_check(client=client)
        # All 3 removals went through; no abort.
        assert result["removed"] == 3
        assert m_bad.call_count == 3

    def test_caller_can_override_rate_threshold(self) -> None:
        # If the caller is confident (e.g. running a manual cleanup
        # after a real moderation event), set max_removal_rate=1.0
        # to disable the guard.
        rows = self._rows(20)
        client = MagicMock()
        client.check_post_alive.return_value = False
        with (
            patch.object(fb_survival_check, "find_candidates", return_value=rows),
            patch.object(fb_survival_check, "mark_removed") as m_bad,
            patch.object(fb_survival_check, "mark_checked"),
        ):
            result = fb_survival_check.run_check(client=client, max_removal_rate=1.0)
        # All 20 written when the guard is opened wide.
        assert result["removed"] == 20
        assert m_bad.call_count == 20

    def test_cli_returns_nonzero_on_guard_trip(self) -> None:
        # Systemd surfaces a non-zero exit, so the alert loop fires.
        with patch.object(
            fb_survival_check,
            "run_check",
            side_effect=fb_survival_check.RemovalRateExceeded("test trip"),
        ):
            exit_code = fb_survival_check.main([])
        assert exit_code == 2


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
