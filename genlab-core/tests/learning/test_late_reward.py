"""Tests for late_reward module (PR Intervention-1).

Covers:
- Feature flag semantics (exact-true)
- recompute_late_reward happy path with mocked metrics
- Fail-closed on DB errors, missing baseline, missing metrics
- process_late_reward_batch iteration
- delta persistence + bandit push guardrails
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from genlab_core.learning import late_reward


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

    def test_returns_none_when_no_baseline(self):
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
            fetch_platform_metrics_fn=lambda *a, **k: {"views": 100},
        )
        assert result is None

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
