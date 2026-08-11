"""Tests for late_reward module (PR Intervention-1).

Covers:
- Feature flag semantics (exact-true)
- recompute_late_reward happy path with mocked metrics
- Fail-closed on DB errors, missing baseline, missing metrics
- process_late_reward_batch iteration
- delta persistence + bandit push guardrails
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from genlab_core.learning import late_reward


class TestPercentileTargetsWireGap:
    """Batch C gap-fill (2026-07-01): late_reward MUST pass
    ``percentile_targets_fn`` when it constructs its own RewardShaper.

    Prod metric_collector wires percentile targets on the 48h reward
    compute. If late_reward's default shaper omitted it, ``reward_late``
    would use hardcoded targets while ``reward_48h`` used percentile
    targets — the ``delta`` between them would measure target-shape
    difference, not actual late-window engagement lift.

    Source pin only — end-to-end behavior is covered by
    test_percentile_targets.py + test_metric_collector.py.
    """

    def test_default_shaper_wires_percentile_targets_fn(self):
        src = inspect.getsource(late_reward.recompute_late_reward)
        assert "get_percentile_target" in src, (
            "recompute_late_reward's default RewardShaper construction "
            "must import get_percentile_target. Dropping the import "
            "silently regresses to hardcoded-target reward computation "
            "which makes ``delta = reward_late - reward_48h`` meaningless."
        )
        assert "percentile_targets_fn=get_percentile_target" in src, (
            "recompute_late_reward's default RewardShaper must be "
            "constructed with ``percentile_targets_fn=get_percentile_target``. "
            "Without this kwarg the shaper.__init__ default of None is used "
            "and the percentile-relative path is silently skipped."
        )


class TestSQLColumnIntegrity:
    """2026-07-02: pin the two column-name bugs that had Intervention 1
    silently no-op'ing in prod for 7+ weeks.

    ``late_reward.py`` originally queried:

      * ``pa.platform_post_id`` — column is called ``post_id``
      * ``p.blueprint_id`` — column doesn't exist in pending_feedback

    Every runner call errored on parse, got caught by the generic
    exception handler with ``logger.warning``, returned None → no
    persist → ``late_reward_deltas`` table stayed empty forever.

    Source pin (fast, no DB) — protects the specific class of bug.
    A parse-time EXPLAIN test would be more robust but requires
    integration test infrastructure; source pin catches the exact
    regression that would silently reintroduce the bug.
    """

    def test_sql_does_not_reference_nonexistent_columns(self):
        src = inspect.getsource(late_reward.recompute_late_reward)
        # The two dead references — mutating either back to the broken
        # form silently kills Intervention 1 again.
        assert "pa.platform_post_id" not in src, (
            "publishing_analytics has no ``platform_post_id`` column — "
            "use ``pa.post_id AS platform_post_id`` if you need the alias."
        )
        assert "p.blueprint_id" not in src, (
            "pending_feedback has no ``blueprint_id`` column — join via "
            "(p.platform, p.post_id) against publishing_analytics."
        )

    def test_sql_uses_working_join_shape(self):
        src = inspect.getsource(late_reward.recompute_late_reward)
        # Positive assertion — the working join must be present. Guards
        # against a refactor that removes columns but doesn't add the
        # correct join.
        assert "p.platform = pa.platform" in src, (
            "pending_feedback join must include ``p.platform = pa.platform``."
        )
        assert "p.post_id" in src, "pending_feedback join must include a ``p.post_id`` predicate."


class TestFeatureFlag:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("GENLAB_MULTI_WINDOW_REWARD_ENABLED", raising=False)
        assert not late_reward._integration_enabled()

    def test_exact_true_only(self, monkeypatch):
        for v in ("true", "TRUE", "True"):
            monkeypatch.setenv("GENLAB_MULTI_WINDOW_REWARD_ENABLED", v)
            assert late_reward._integration_enabled()
        for v in ("1", "yes", "on", "false", ""):
            monkeypatch.setenv("GENLAB_MULTI_WINDOW_REWARD_ENABLED", v)
            assert not late_reward._integration_enabled(), f"unexpected truthy: {v!r}"


class TestRecomputeLateReward:
    def _mock_conn(self, row):
        conn = MagicMock()

        def _execute(sql, params=None):
            result = MagicMock()
            result.fetchone.return_value = row
            return result

        conn.execute.side_effect = _execute
        return conn

    def test_returns_none_when_no_row(self):
        conn = self._mock_conn(row=None)
        result = late_reward.recompute_late_reward(
            "abc",
            conn=conn,
            shaper=MagicMock(),
            fetch_platform_metrics_fn=lambda *a, **k: {},
        )
        assert result is None

    def test_returns_none_when_no_baseline_and_late_fetch_empty(self):
        """No baseline + empty late metrics → still None (no signal to
        backfill from). Task B backfill only fires when reward_late is
        computable."""
        conn = self._mock_conn(
            row={
                "id": "abc",
                "niche_id": "gaming",
                "arm_id": "style:revelation",
                "platform": "instagram",
                "platform_post_id": "post123",
                "published_at": None,
                "reward_48h": None,
            }
        )
        result = late_reward.recompute_late_reward(
            "abc",
            conn=conn,
            shaper=MagicMock(),
            fetch_platform_metrics_fn=lambda *a, **k: {},  # empty metrics
        )
        assert result is None

    def test_backfill_fires_when_no_baseline_and_late_reward_available(self, caplog):
        """2026-08-11 Task B: reward_48h was NULL (from Task A premature-
        fetch on IG/Threads). Now that 168h has real data, backfill
        reward_late as the first authoritative reward_48h so the bandit
        posterior picks it up on next backfill cycle.

        Regression pin: if a future refactor removes the backfill path,
        sports IG returns to the 0/16 positive rewards pathology."""
        import logging

        conn = self._mock_conn(
            row={
                "id": "abc",
                "niche_id": "sports",
                "arm_id": "hour:12:instagram:sports",
                "platform": "instagram",
                "platform_post_id": "instagram:post123",
                "published_at": None,
                "reward_48h": None,  # Task A left this NULL
            }
        )
        shaper = MagicMock()
        shaper.compute_reward.return_value = 0.42  # computed from 168h real data

        with caplog.at_level(logging.INFO):
            result = late_reward.recompute_late_reward(
                "abc",
                conn=conn,
                shaper=shaper,
                fetch_platform_metrics_fn=lambda *a, **k: {"views": 500, "likes": 10},
            )

        # Return is None (no delta to record, since no baseline exists)
        assert result is None, (
            "backfill path returns None (no delta measurement to record)"
        )

        # INFO log confirms the backfill happened
        assert any(
            "late_reward.backfilled" in r.message
            and "premature-fetch recovery" in r.message
            for r in caplog.records
        ), (
            "Task B must emit late_reward.backfilled INFO log so operators "
            "can grep for the recovery path in journalctl."
        )

        # Conn.execute was called with an UPDATE — the backfill SQL fired
        update_calls = [
            call for call in conn.execute.call_args_list
            if "UPDATE pending_feedback" in str(call).replace("\\n", " ")
        ]
        assert update_calls, (
            "Task B backfill must execute UPDATE pending_feedback SET "
            "reward_48h=<reward_late>. Regression: reverting this path "
            "re-introduces the sports-IG 0/16 positive-reward pathology."
        )

    def test_sql_status_filter_includes_insights_windows(self):
        """2026-08-11 Bug 1 regression pin: the SQL WHERE clause must
        NOT filter status='SUCCESS' alone. That filter was silently
        killing late_reward for 20 days (Jul 22 → Aug 11) because
        posts transition SUCCESS → INSIGHTS_6H/24H/48H/168H within
        48h of publish. By the 6-8-days-ago window that late_reward
        scans, no row has status='SUCCESS' anymore — zero matches
        → zero processing → the entire late-tail correction system
        was dead.

        Fix: include INSIGHTS_* variants (any status representing a
        successfully-published post progressing through its metric-
        collection lifecycle). Explicitly EXCLUDE REMOVED_BY_META /
        FAILED / PARTIAL.

        Regression: reverting to status='SUCCESS' alone re-introduces
        the 20-day silent-death pattern."""
        import inspect

        source = inspect.getsource(late_reward.process_late_reward_batch)
        # New contract: uses IN () with multiple statuses
        assert "status IN" in source or "status in" in source, (
            "process_late_reward_batch SQL must use `status IN (...)` "
            "with multiple lifecycle statuses. The old `status = 'SUCCESS'` "
            "single-value filter was the load-bearing bug that killed "
            "late_reward for 20 days."
        )
        # Include INSIGHTS_168H specifically — that's what most 6-8d
        # posts are in by the time late_reward scans them
        assert "INSIGHTS_168H" in source or "INSIGHTS_48H" in source, (
            "SQL must include INSIGHTS_* variants — that's where "
            "6-8-day-old posts have transitioned to."
        )

        source_recompute = inspect.getsource(late_reward.recompute_late_reward)
        assert "status IN" in source_recompute or "status in" in source_recompute, (
            "recompute_late_reward SQL must use the same expanded status "
            "filter as process_late_reward_batch. Both had the same bug."
        )

    def test_no_backfill_when_baseline_exists(self):
        """Regression pin: when reward_48h has a value already, DON'T
        backfill — normal delta measurement path is authoritative."""
        conn = self._mock_conn(
            row={
                "id": "abc",
                "niche_id": "gaming",
                "arm_id": "style:x",
                "platform": "instagram",
                "platform_post_id": "post123",
                "published_at": None,
                "reward_48h": 0.15,  # has baseline
            }
        )
        shaper = MagicMock()
        shaper.compute_reward.return_value = 0.30

        result = late_reward.recompute_late_reward(
            "abc",
            conn=conn,
            shaper=shaper,
            fetch_platform_metrics_fn=lambda *a, **k: {"views": 500, "likes": 5},
        )

        assert result is not None
        assert result.reward_48h == pytest.approx(0.15)
        assert result.reward_late == pytest.approx(0.30)

        # No UPDATE fired — only the SELECT for the row read
        update_calls = [
            call for call in conn.execute.call_args_list
            if "UPDATE pending_feedback" in str(call).replace("\\n", " ")
        ]
        assert not update_calls, (
            "When reward_48h has a value, we should NOT backfill — the "
            "existing value is authoritative and only delta gets recorded."
        )

    def test_computes_delta_correctly(self):
        conn = self._mock_conn(
            row={
                "id": "abc",
                "niche_id": "gaming",
                "arm_id": "style:revelation",
                "platform": "instagram",
                "platform_post_id": "post123",
                "published_at": None,
                "reward_48h": 0.20,
            }
        )
        shaper = MagicMock()
        shaper.compute_reward.return_value = 0.35  # 75% lift
        result = late_reward.recompute_late_reward(
            "abc",
            conn=conn,
            shaper=shaper,
            fetch_platform_metrics_fn=lambda *a, **k: {"views": 500},
        )
        assert result is not None
        assert result.reward_48h == pytest.approx(0.20)
        assert result.reward_late == pytest.approx(0.35)
        assert result.delta == pytest.approx(0.15)
        assert result.delta_pct == pytest.approx(0.75)
        assert result.arm_id == "style:revelation"

    def test_fetch_failure_returns_none(self):
        conn = self._mock_conn(
            row={
                "id": "abc",
                "niche_id": "gaming",
                "arm_id": "style:revelation",
                "platform": "instagram",
                "platform_post_id": "post123",
                "published_at": None,
                "reward_48h": 0.15,
            }
        )

        def _raise(*a, **k):
            raise RuntimeError("API down")

        result = late_reward.recompute_late_reward(
            "abc", conn=conn, shaper=MagicMock(), fetch_platform_metrics_fn=_raise
        )
        assert result is None

    def test_empty_metrics_returns_none(self):
        conn = self._mock_conn(
            row={
                "id": "abc",
                "niche_id": "gaming",
                "arm_id": "style:revelation",
                "platform": "instagram",
                "platform_post_id": "post123",
                "published_at": None,
                "reward_48h": 0.15,
            }
        )
        result = late_reward.recompute_late_reward(
            "abc",
            conn=conn,
            shaper=MagicMock(),
            fetch_platform_metrics_fn=lambda *a, **k: {},
        )
        assert result is None


class TestBanditPushGuardrails:
    def test_small_delta_does_not_push(self, monkeypatch):
        """delta_pct=10% < 20% material threshold → no bandit push."""
        monkeypatch.setenv("GENLAB_MULTI_WINDOW_REWARD_ENABLED", "true")

        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [{"blueprint_id": "abc"}]

        with patch.object(late_reward, "recompute_late_reward") as mock_recompute:
            mock_recompute.return_value = late_reward.LateRewardDelta(
                blueprint_id="abc",
                niche_id="gaming",
                arm_id="style:revelation",
                platform="instagram",
                reward_48h=0.20,
                reward_late=0.22,
                window_days=7,
                delta=0.02,
                delta_pct=0.10,  # below 20% threshold
                measured_at=datetime.now(UTC),
            )
            with patch.object(late_reward, "_persist_delta_row"):
                with patch.object(late_reward, "_push_delta_to_bandit") as mock_push:
                    late_reward.process_late_reward_batch(conn=conn)
        mock_push.assert_not_called()

    def test_large_delta_pushes_when_flag_on(self, monkeypatch):
        """delta_pct=50% > 20% threshold AND flag on → push to bandit."""
        monkeypatch.setenv("GENLAB_MULTI_WINDOW_REWARD_ENABLED", "true")

        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [{"blueprint_id": "abc"}]

        with patch.object(late_reward, "recompute_late_reward") as mock_recompute:
            mock_recompute.return_value = late_reward.LateRewardDelta(
                blueprint_id="abc",
                niche_id="gaming",
                arm_id="style:revelation",
                platform="instagram",
                reward_48h=0.20,
                reward_late=0.30,
                window_days=7,
                delta=0.10,
                delta_pct=0.50,  # above 20% threshold
                measured_at=datetime.now(UTC),
            )
            with patch.object(late_reward, "_persist_delta_row"):
                with patch.object(late_reward, "_push_delta_to_bandit") as mock_push:
                    late_reward.process_late_reward_batch(conn=conn)
        mock_push.assert_called_once()

    def test_large_delta_does_not_push_when_flag_off(self, monkeypatch):
        """Even with 50% lift, flag off means NO bandit push."""
        monkeypatch.delenv("GENLAB_MULTI_WINDOW_REWARD_ENABLED", raising=False)

        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [{"blueprint_id": "abc"}]

        with patch.object(late_reward, "recompute_late_reward") as mock_recompute:
            mock_recompute.return_value = late_reward.LateRewardDelta(
                blueprint_id="abc",
                niche_id="gaming",
                arm_id="style:revelation",
                platform="instagram",
                reward_48h=0.20,
                reward_late=0.30,
                window_days=7,
                delta=0.10,
                delta_pct=0.50,
                measured_at=datetime.now(UTC),
            )
            with patch.object(late_reward, "_persist_delta_row"):
                with patch.object(late_reward, "_push_delta_to_bandit") as mock_push:
                    late_reward.process_late_reward_batch(conn=conn)
        mock_push.assert_not_called()


class TestBatch:
    def test_counters_populated(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgres://fake")
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [
            {"blueprint_id": "a"},
            {"blueprint_id": "b"},
            {"blueprint_id": "c"},
        ]

        with patch.object(late_reward, "recompute_late_reward") as mock_recompute:
            # 2 blueprints measured, 1 skipped
            mock_recompute.side_effect = [
                late_reward.LateRewardDelta(
                    blueprint_id="a",
                    niche_id="gaming",
                    arm_id="s:r",
                    platform="ig",
                    reward_48h=0.1,
                    reward_late=0.15,
                    window_days=7,
                    delta=0.05,
                    delta_pct=0.5,  # material lift
                    measured_at=datetime.now(UTC),
                ),
                None,
                late_reward.LateRewardDelta(
                    blueprint_id="c",
                    niche_id="gaming",
                    arm_id="s:r",
                    platform="ig",
                    reward_48h=0.1,
                    reward_late=0.11,
                    window_days=7,
                    delta=0.01,
                    delta_pct=0.10,  # NOT material
                    measured_at=datetime.now(UTC),
                ),
            ]
            with patch.object(late_reward, "_persist_delta_row"):
                counters = late_reward.process_late_reward_batch(conn=conn)

        assert counters["scanned"] == 3
        assert counters["measured"] == 2
        assert counters["significant_lift"] == 1


class TestPlatformScopedRecompute:
    """2026-07-23: recompute_late_reward now accepts a platform filter.

    Pre-fix: LIMIT 1 picked whichever row Postgres returned first for a
    blueprint that published to multiple platforms. Historically FB (row
    insertion order). Result: late_reward_deltas table only had FB rows
    despite blueprints publishing to 4-5 platforms each.

    Post-fix: batch iterates (blueprint × platform) pairs → each pair
    gets its own delta row → 4-5x more coverage for IG/YT/Threads
    long-tail growth patterns.
    """

    def test_batch_query_returns_bp_platform_pairs(self, monkeypatch):
        """Verify the batch fetches (blueprint_id, platform) tuples,
        not just blueprint_ids. This is the shape change that unlocks
        per-platform late-reward measurement."""
        monkeypatch.setenv("DATABASE_URL", "postgres://fake")
        conn = MagicMock()
        # Batch's new SQL returns rows with BOTH blueprint_id + platform
        conn.execute.return_value.fetchall.return_value = [
            {"blueprint_id": "bp1", "platform": "facebook"},
            {"blueprint_id": "bp1", "platform": "instagram"},
            {"blueprint_id": "bp1", "platform": "youtube"},
            {"blueprint_id": "bp1", "platform": "threads"},
        ]

        called_platforms: list[str] = []

        def _capture(bp_id, *, conn=None, platform=""):
            called_platforms.append(platform)
            return None  # skip persist path

        with patch.object(late_reward, "recompute_late_reward", side_effect=_capture):
            with patch.object(late_reward, "_persist_delta_row"):
                counters = late_reward.process_late_reward_batch(conn=conn)

        # 4 (bp × platform) pairs scanned — one per platform
        assert counters["scanned"] == 4
        # Each recompute call got a distinct platform, not empty
        assert called_platforms == ["facebook", "instagram", "youtube", "threads"]

    def test_recompute_with_platform_filter_narrows_query(self, monkeypatch):
        """When platform is passed to recompute_late_reward, the SQL
        must include ``AND pa.platform = %s`` so LIMIT 1 picks that
        specific platform's row (not whichever was inserted first)."""
        monkeypatch.setenv("DATABASE_URL", "postgres://fake")
        conn = MagicMock()

        # No matching row for this platform → returns None
        conn.execute.return_value.fetchone.return_value = None

        result = late_reward.recompute_late_reward(
            "bp_abc", conn=conn, platform="youtube"
        )
        assert result is None

        # Verify the query included the platform filter
        # (2nd param since bp_id is 1st)
        call_args = conn.execute.call_args
        query_str = call_args[0][0]
        query_params = call_args[0][1]
        assert "pa.platform = %s" in query_str
        assert query_params == ("bp_abc", "youtube")

    def test_recompute_without_platform_filter_preserves_legacy_shape(
        self, monkeypatch
    ):
        """Empty-string platform (default) MUST use the pre-fix SQL —
        no platform filter — to preserve backward compat for any legacy
        caller that hasn't been updated to pass platform."""
        monkeypatch.setenv("DATABASE_URL", "postgres://fake")
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = None

        late_reward.recompute_late_reward("bp_abc", conn=conn)

        call_args = conn.execute.call_args
        query_str = call_args[0][0]
        query_params = call_args[0][1]
        # Legacy shape: no platform filter substring
        assert "pa.platform = %s" not in query_str
        # Only bp_id in params
        assert query_params == ("bp_abc",)
