"""Tests for optimal_time_learner.pick_optimal_hour_for_niche.

This is the multi-platform aggregate hour picker used by
nightly_schedule_top_per_niche.compute_target_slot. Each niche gets
a per-niche scheduled_for hour chosen from the sum of its
hour:{H}:{platform}:{niche} bandit posteriors.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestFlagGate:
    def test_flag_off_returns_fallback(self, monkeypatch):
        from genlab_core.scheduling.optimal_time_learner import (
            pick_optimal_hour_for_niche,
        )

        monkeypatch.delenv("GENLAB_OPTIMAL_TIME_BANDIT_ENABLED", raising=False)
        monkeypatch.delenv("GENLAB_OPTIMAL_TIME_BANDIT", raising=False)

        hour, source = pick_optimal_hour_for_niche("gaming", fallback_hour=6)
        assert hour == 6
        assert source == "fallback"


class TestColdStart:
    def _enable_flag(self, monkeypatch):
        monkeypatch.setenv("GENLAB_OPTIMAL_TIME_BANDIT_ENABLED", "1")

    def test_empty_arms_returns_fallback(self, monkeypatch):
        from genlab_core.scheduling import optimal_time_learner as mod

        self._enable_flag(monkeypatch)

        mock_proxy = MagicMock()
        mock_client = MagicMock()
        mock_client.bandit_arms = mock_proxy
        with (
            patch("genlab_core.http.backlog_client.BacklogClient", return_value=mock_client),
            patch(
                "genlab_core.learning.arm_loader.load_all_arms",
                return_value={},
            ),
        ):
            hour, source = mod.pick_optimal_hour_for_niche(
                "gaming", fallback_hour=6, min_total_obs=30
            )
        assert hour == 6
        assert source == "fallback"

    def test_below_min_obs_returns_fallback(self, monkeypatch):
        """Arms exist but total observations across all hours < min —
        posteriors still noise-dominated by Beta(1,1) prior."""
        from genlab_core.scheduling import optimal_time_learner as mod

        self._enable_flag(monkeypatch)

        # 3 arms, each at Beta(1.5, 1.5) — total_obs = 3 * (3 - 2) = 3.
        # Below the 30-obs threshold.
        arms = {
            "hour:6:youtube:gaming": (1.5, 1.5),
            "hour:12:youtube:gaming": (1.5, 1.5),
            "hour:18:youtube:gaming": (1.5, 1.5),
        }
        mock_proxy = MagicMock()
        mock_client = MagicMock()
        mock_client.bandit_arms = mock_proxy
        with (
            patch("genlab_core.http.backlog_client.BacklogClient", return_value=mock_client),
            patch(
                "genlab_core.learning.arm_loader.load_all_arms",
                return_value=arms,
            ),
        ):
            hour, source = mod.pick_optimal_hour_for_niche(
                "gaming", fallback_hour=6, min_total_obs=30
            )
        assert source == "fallback"
        assert hour == 6


class TestBanditPick:
    def _enable_flag(self, monkeypatch):
        monkeypatch.setenv("GENLAB_OPTIMAL_TIME_BANDIT_ENABLED", "1")

    def test_prefers_higher_reward_hour(self, monkeypatch):
        """Hour 18 has α=100, β=10 (heavily favored); hour 6 has α=10,
        β=100 (heavily disfavored). Bandit MUST pick 18 with very
        high probability."""
        from genlab_core.scheduling import optimal_time_learner as mod

        self._enable_flag(monkeypatch)

        arms = {
            "hour:6:youtube:gaming": (10.0, 100.0),   # 100 obs, ~9% ctr
            "hour:18:youtube:gaming": (100.0, 10.0),  # 110 obs, ~91% ctr
            "hour:12:youtube:gaming": (30.0, 30.0),   # 60 obs, ~50%
        }
        mock_proxy = MagicMock()
        mock_client = MagicMock()
        mock_client.bandit_arms = mock_proxy

        picks: list[int] = []
        with (
            patch("genlab_core.http.backlog_client.BacklogClient", return_value=mock_client),
            patch(
                "genlab_core.learning.arm_loader.load_all_arms",
                return_value=arms,
            ),
        ):
            for _ in range(20):
                hour, source = mod.pick_optimal_hour_for_niche(
                    "gaming", fallback_hour=6, min_total_obs=30
                )
                assert source == "bandit"
                picks.append(hour)

        # Hour 18 should dominate — expect at least 18/20 picks.
        eighteen_count = sum(1 for h in picks if h == 18)
        assert eighteen_count >= 18, (
            f"Expected hour 18 in ≥18/20 picks (strongly favored posterior); "
            f"got {eighteen_count}. Picks: {picks}"
        )

    def test_aggregates_across_platforms(self, monkeypatch):
        """Hour 18 has weaker per-platform arms but combined they
        outweigh hour 6's single strong arm. Bandit MUST sum across
        platforms before picking."""
        from genlab_core.scheduling import optimal_time_learner as mod

        self._enable_flag(monkeypatch)

        arms = {
            # Hour 6: single-platform strong signal
            "hour:6:youtube:gaming": (50.0, 10.0),
            # Hour 18: 3 platforms each moderate → combined stronger
            "hour:18:youtube:gaming": (30.0, 5.0),
            "hour:18:instagram:gaming": (30.0, 5.0),
            "hour:18:facebook:gaming": (30.0, 5.0),
        }
        mock_proxy = MagicMock()
        mock_client = MagicMock()
        mock_client.bandit_arms = mock_proxy

        picks: list[int] = []
        with (
            patch("genlab_core.http.backlog_client.BacklogClient", return_value=mock_client),
            patch(
                "genlab_core.learning.arm_loader.load_all_arms",
                return_value=arms,
            ),
        ):
            for _ in range(20):
                hour, _ = mod.pick_optimal_hour_for_niche(
                    "gaming", fallback_hour=6, min_total_obs=30
                )
                picks.append(hour)

        # Combined hour 18 posterior α+β = 90+15 = 105, mean ~0.857
        # Hour 6 posterior α+β = 60, mean ~0.833
        # Sampling variance will favor 18 due to lower relative variance.
        eighteen_count = sum(1 for h in picks if h == 18)
        assert eighteen_count >= 12, (
            f"Aggregated 3-platform hour 18 should beat 1-platform hour 6 "
            f"in ≥12/20 picks; got {eighteen_count}. Picks: {picks}"
        )


class TestNicheIsolation:
    def _enable_flag(self, monkeypatch):
        monkeypatch.setenv("GENLAB_OPTIMAL_TIME_BANDIT_ENABLED", "1")

    def test_does_not_count_other_niche_arms(self, monkeypatch):
        """A ``hour:18:youtube:anime`` arm MUST NOT influence
        gaming's pick — bandit isolation is per-niche."""
        from genlab_core.scheduling import optimal_time_learner as mod

        self._enable_flag(monkeypatch)

        # Gaming has only its own weak signal; anime has strong hour 18
        # signal we should NOT pick up.
        arms = {
            "hour:6:youtube:gaming": (5.0, 3.0),
            "hour:12:youtube:gaming": (5.0, 3.0),
            "hour:18:youtube:gaming": (5.0, 3.0),
            "hour:18:youtube:anime": (200.0, 10.0),  # decoy — different niche
        }
        mock_proxy = MagicMock()
        mock_client = MagicMock()
        mock_client.bandit_arms = mock_proxy

        with (
            patch("genlab_core.http.backlog_client.BacklogClient", return_value=mock_client),
            patch(
                "genlab_core.learning.arm_loader.load_all_arms",
                return_value=arms,
            ),
        ):
            # Total obs for gaming = 3 arms * (5+3-2) = 18 < 30 threshold
            hour, source = mod.pick_optimal_hour_for_niche(
                "gaming", fallback_hour=6, min_total_obs=30
            )
        # Must be fallback — anime's decoy 200+10 obs must not be counted.
        assert source == "fallback"
        assert hour == 6
