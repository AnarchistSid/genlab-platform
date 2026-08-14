"""Pin Phase 5.C session 1 flag-flip proposer:

  * Registry has 4 proposers
  * Style guidance: bumps when lift >= 15%
  * Style guidance: no bump when lift < 15%
  * Style guidance: no bump when sample size below floor
  * Style guidance: ladder step 25 -> 50 -> 75 -> 100
  * Ideation pool: gated by flag ON + rollout < 100
  * Ideation pool: pool_mean < trending_mean → no bump
  * Autonomous reviewer: needs >= 3 types covered
  * Autonomous reviewer: skip if quality worse than operator
  * Quality multiplier: gate on corr >= 0.20
  * Quality multiplier: gate on n >= 50
  * Rule #22: every rationale includes raw evidence numbers
  * collect_proposals aggregates + skips crashed proposers
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from genlab_core.scheduling.flag_flip_proposer import (
    FlagFlipProposal,
    _PROPOSER_REGISTRY,
    _propose_autonomous_reviewer_enable,
    _propose_ideation_pool_rollout,
    _propose_quality_reward_multiplier_enable,
    _propose_style_guidance_rollout,
    collect_proposals,
)


class TestRegistry:
    def test_four_proposers_registered(self):
        assert len(_PROPOSER_REGISTRY) == 4

    def test_each_proposer_is_callable(self):
        for fn in _PROPOSER_REGISTRY:
            assert callable(fn)


class TestStyleGuidanceRollout:
    def _conn(self, n_c: int, n_t: int, mean_c: float | None, mean_t: float | None):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {
            "n_c": n_c, "n_t": n_t,
            "mean_c": mean_c, "mean_t": mean_t,
        }
        return conn

    def test_lift_above_gate_proposes(self, monkeypatch):
        monkeypatch.setenv("GENLAB_STYLE_GUIDANCE_ROLLOUT_PCT", "25")
        # mean_t 0.115 vs 0.10 → lift 15%
        conn = self._conn(n_c=50, n_t=30, mean_c=0.10, mean_t=0.115)
        p = _propose_style_guidance_rollout(conn)
        assert p is not None
        assert p.flag_name == "GENLAB_STYLE_GUIDANCE_ROLLOUT_PCT"
        assert p.from_state == "25"
        assert p.to_state == "50"

    def test_lift_below_gate_no_propose(self, monkeypatch):
        monkeypatch.setenv("GENLAB_STYLE_GUIDANCE_ROLLOUT_PCT", "25")
        conn = self._conn(n_c=50, n_t=30, mean_c=0.10, mean_t=0.105)  # +5% only
        assert _propose_style_guidance_rollout(conn) is None

    def test_sample_size_below_floor_no_propose(self, monkeypatch):
        monkeypatch.setenv("GENLAB_STYLE_GUIDANCE_ROLLOUT_PCT", "25")
        conn = self._conn(n_c=5, n_t=5, mean_c=0.10, mean_t=0.15)  # tiny n
        assert _propose_style_guidance_rollout(conn) is None

    def test_already_100_no_propose(self, monkeypatch):
        monkeypatch.setenv("GENLAB_STYLE_GUIDANCE_ROLLOUT_PCT", "100")
        conn = self._conn(n_c=50, n_t=30, mean_c=0.10, mean_t=0.15)
        assert _propose_style_guidance_rollout(conn) is None

    @pytest.mark.parametrize("current,expected", [
        ("0", "25"), ("25", "50"), ("50", "75"), ("75", "100"),
    ])
    def test_ladder_next_step(self, monkeypatch, current, expected):
        monkeypatch.setenv("GENLAB_STYLE_GUIDANCE_ROLLOUT_PCT", current)
        conn = self._conn(n_c=50, n_t=30, mean_c=0.10, mean_t=0.13)  # +30%
        p = _propose_style_guidance_rollout(conn)
        assert p is not None
        assert p.to_state == expected

    def test_rationale_includes_raw_evidence(self, monkeypatch):
        """Rule #22: rationale must show lift + n's, not just aggregate."""
        monkeypatch.setenv("GENLAB_STYLE_GUIDANCE_ROLLOUT_PCT", "25")
        conn = self._conn(n_c=50, n_t=30, mean_c=0.10, mean_t=0.13)
        p = _propose_style_guidance_rollout(conn)
        assert "n_control=50" in p.rationale
        assert "n_treatment=30" in p.rationale


class TestIdeationPoolRollout:
    def _conn(self, n_pool, n_trending, mean_pool, mean_trend):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {
            "n_pool": n_pool, "n_trending": n_trending,
            "mean_pool": mean_pool, "mean_trend": mean_trend,
        }
        return conn

    def test_flag_off_no_propose(self, monkeypatch):
        monkeypatch.delenv("GENLAB_IDEATION_POOL_ENABLED", raising=False)
        monkeypatch.setenv("GENLAB_IDEATION_POOL_ROLLOUT_PCT", "25")
        conn = self._conn(20, 100, 0.15, 0.10)
        assert _propose_ideation_pool_rollout(conn) is None

    def test_pool_worse_than_trending_no_propose(self, monkeypatch):
        monkeypatch.setenv("GENLAB_IDEATION_POOL_ENABLED", "1")
        monkeypatch.setenv("GENLAB_IDEATION_POOL_ROLLOUT_PCT", "25")
        conn = self._conn(20, 100, 0.05, 0.10)  # pool lower
        assert _propose_ideation_pool_rollout(conn) is None

    def test_pool_matches_trending_proposes(self, monkeypatch):
        monkeypatch.setenv("GENLAB_IDEATION_POOL_ENABLED", "1")
        monkeypatch.setenv("GENLAB_IDEATION_POOL_ROLLOUT_PCT", "25")
        conn = self._conn(20, 100, 0.11, 0.10)  # slight lift
        p = _propose_ideation_pool_rollout(conn)
        assert p is not None
        assert p.to_state == "50"

    def test_low_sample_no_propose(self, monkeypatch):
        monkeypatch.setenv("GENLAB_IDEATION_POOL_ENABLED", "1")
        monkeypatch.setenv("GENLAB_IDEATION_POOL_ROLLOUT_PCT", "25")
        conn = self._conn(3, 100, 0.15, 0.10)  # n_pool too low
        assert _propose_ideation_pool_rollout(conn) is None


class TestAutonomousReviewerEnable:
    def _conn(self, n_total, n_types, auto_imp, auto_reg, op_imp, op_reg):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {
            "n_total": n_total, "n_types_covered": n_types,
            "auto_imp": auto_imp, "auto_reg": auto_reg,
            "op_imp": op_imp, "op_reg": op_reg,
        }
        return conn

    def test_already_on_no_propose(self, monkeypatch):
        monkeypatch.setenv("GENLAB_AUTONOMOUS_REVIEWER_ENABLED", "1")
        conn = self._conn(50, 4, 40, 5, 4, 1)
        assert _propose_autonomous_reviewer_enable(conn) is None

    def test_insufficient_types_no_propose(self, monkeypatch):
        monkeypatch.delenv("GENLAB_AUTONOMOUS_REVIEWER_ENABLED", raising=False)
        conn = self._conn(50, 2, 40, 5, 4, 1)  # only 2 types covered
        assert _propose_autonomous_reviewer_enable(conn) is None

    def test_worse_quality_no_propose(self, monkeypatch):
        monkeypatch.delenv("GENLAB_AUTONOMOUS_REVIEWER_ENABLED", raising=False)
        # auto quality 60%, op quality 95% → skip
        conn = self._conn(50, 3, 6, 4, 19, 1)
        assert _propose_autonomous_reviewer_enable(conn) is None

    def test_matching_quality_proposes(self, monkeypatch):
        monkeypatch.delenv("GENLAB_AUTONOMOUS_REVIEWER_ENABLED", raising=False)
        # auto quality 80%, op quality 85% (within tolerance)
        conn = self._conn(50, 3, 40, 10, 17, 3)
        p = _propose_autonomous_reviewer_enable(conn)
        assert p is not None
        assert p.to_state == "1"


class TestQualityMultiplierEnable:
    def _conn(self, n, corr):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {"n": n, "corr": corr}
        return conn

    def test_low_n_no_propose(self, monkeypatch):
        monkeypatch.delenv(
            "GENLAB_QUALITY_REWARD_MULTIPLIER_ENABLED", raising=False,
        )
        conn = self._conn(20, 0.5)  # n too low
        assert _propose_quality_reward_multiplier_enable(conn) is None

    def test_weak_corr_no_propose(self, monkeypatch):
        monkeypatch.delenv(
            "GENLAB_QUALITY_REWARD_MULTIPLIER_ENABLED", raising=False,
        )
        conn = self._conn(100, 0.10)  # corr too weak
        assert _propose_quality_reward_multiplier_enable(conn) is None

    def test_strong_corr_proposes(self, monkeypatch):
        monkeypatch.delenv(
            "GENLAB_QUALITY_REWARD_MULTIPLIER_ENABLED", raising=False,
        )
        conn = self._conn(100, 0.35)
        p = _propose_quality_reward_multiplier_enable(conn)
        assert p is not None
        assert p.rationale.startswith("joint_score-reward correlation")


class TestCollectProposals:
    def test_aggregates_across_all_proposers(self, monkeypatch):
        """When every proposer returns a proposal, collect returns all 4."""
        monkeypatch.setenv("GENLAB_STYLE_GUIDANCE_ROLLOUT_PCT", "25")
        monkeypatch.setenv("GENLAB_IDEATION_POOL_ENABLED", "1")
        monkeypatch.setenv("GENLAB_IDEATION_POOL_ROLLOUT_PCT", "25")
        monkeypatch.delenv("GENLAB_AUTONOMOUS_REVIEWER_ENABLED", raising=False)
        monkeypatch.delenv(
            "GENLAB_QUALITY_REWARD_MULTIPLIER_ENABLED", raising=False,
        )

        conn = MagicMock()
        # Each execute call returns a different aggregate based on
        # order of proposer registration. Simplest: return one dict
        # that satisfies every schema.
        conn.execute.return_value.fetchone.return_value = {
            # style guidance keys
            "n_c": 50, "n_t": 30, "mean_c": 0.10, "mean_t": 0.13,
            # ideation keys
            "n_pool": 20, "n_trending": 100,
            "mean_pool": 0.15, "mean_trend": 0.10,
            # autonomous keys
            "n_total": 50, "n_types_covered": 3,
            "auto_imp": 40, "auto_reg": 10, "op_imp": 17, "op_reg": 3,
            # quality keys
            "n": 100, "corr": 0.35,
        }
        proposals = collect_proposals(conn)
        assert len(proposals) == 4

    def test_crashed_proposer_skipped(self, monkeypatch):
        """A proposer that raises must not kill the run — collect
        catches + continues."""
        from genlab_core.scheduling import flag_flip_proposer as m

        def _crash(_conn):
            raise RuntimeError("boom")

        monkeypatch.setattr(m, "_PROPOSER_REGISTRY", (_crash,))
        conn = MagicMock()
        # Should return [] without raising
        assert collect_proposals(conn) == []
