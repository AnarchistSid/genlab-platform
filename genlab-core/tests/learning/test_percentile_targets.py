"""Tests for genlab_core.learning.percentile_targets + RewardShaper integration.

Pins fix #6 of the autonomy roadmap: percentile-relative reward targets.
Pre-fix the hardcoded YT views=200 target produced near-zero rewards on
niches with sub-200-view distributions (ai_creators YT avg 0.9) AND
saturated at 1.0 on niches with above-200 distributions (sports YT avg
689) — the bandit couldn't distinguish content quality in either case.

After fix: target self-calibrates to the 70th-percentile of recent
observations, so top 30% always yield reward ≥ 0.7 regardless of
absolute counts.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from genlab_core.learning.percentile_targets import (
    _MIN_OBSERVATIONS,
    _quantile,
    get_percentile_target,
    reset_cache,
)
from genlab_core.learning.reward_shaper import RewardShaper


@pytest.fixture(autouse=True)
def clear_cache():
    reset_cache()
    yield
    reset_cache()


class TestQuantile:
    """Pin the stdlib-only quantile implementation against numpy semantics."""

    def test_70th_pctile_of_known_distribution(self):
        # [10, 20, 30, ..., 100] — 70th = 73.0 by linear interpolation
        # (pos = 0.7 * 9 = 6.3 → between vs[6]=70 and vs[7]=80 → 70 + 0.3*10 = 73)
        vs = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        assert _quantile(vs, 0.70) == pytest.approx(73.0)

    def test_70th_pctile_skewed_distribution(self):
        # The realistic shape: most values low, a few high. The 70th pctile
        # should land above the cluster, providing meaningful reward gradient.
        vs = [10] * 10 + [50] * 5 + [500]
        # 70th of 16 values: pos = 0.7 * 15 = 10.5; sorted: 10,10,10,...,50,50,50,50,50,500
        # vs[10]=50, vs[11]=50 → 50 + 0.5 * 0 = 50.
        assert _quantile(vs, 0.70) == pytest.approx(50.0)

    def test_empty_returns_zero(self):
        assert _quantile([], 0.70) == 0.0

    def test_single_value_returns_value(self):
        assert _quantile([42.0], 0.70) == 42.0


class TestGetPercentileTarget:
    def _mock_db(self, values: list[float]) -> MagicMock:
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [(v,) for v in values]
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = None
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_conn.cursor.return_value.__exit__.return_value = None
        return mock_conn

    def test_returns_70th_pctile_when_enough_observations(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://t")
        mock_conn = self._mock_db([float(v) for v in range(1, 31)])  # 30 vals
        with patch("psycopg.connect", return_value=mock_conn):
            target = get_percentile_target("gaming", "youtube", "views")
        # 70th of 1..30 = 21.3
        assert target is not None
        assert target == pytest.approx(21.3)

    def test_returns_none_below_min_observations(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://t")
        mock_conn = self._mock_db([10.0, 20.0, 30.0])  # well below _MIN_OBSERVATIONS
        with patch("psycopg.connect", return_value=mock_conn):
            target = get_percentile_target("gaming", "youtube", "views")
        assert target is None
        # Sanity: the threshold actually matters.
        assert _MIN_OBSERVATIONS > 3

    def test_returns_none_when_no_database_url(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert get_percentile_target("gaming", "youtube", "views") is None

    def test_returns_none_on_db_exception(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://t")
        with patch("psycopg.connect", side_effect=RuntimeError("db gone")):
            target = get_percentile_target("gaming", "youtube", "views")
        assert target is None

    def test_caches_result(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://t")
        mock_conn = self._mock_db([float(v) for v in range(1, 31)])
        with patch("psycopg.connect", return_value=mock_conn) as mock_connect:
            get_percentile_target("gaming", "youtube", "views")
            get_percentile_target("gaming", "youtube", "views")
            get_percentile_target("gaming", "youtube", "views")
        # Only one DB connection across 3 calls (cache hit)
        assert mock_connect.call_count == 1


class TestRewardShaperUsesPercentileTarget:
    """End-to-end: RewardShaper with percentile_targets_fn should produce
    higher rewards on signal-rich data than the hardcoded fallback."""

    def test_percentile_target_dominates_hardcoded_when_present(self):
        """Signal-rich niche where recent posts have target=1500 (way
        above the hardcoded 200 for views/youtube). A 1000-view post
        should yield ~0.67 (1000/1500) instead of 1.0-capped under the
        hardcoded target. Both are useful signal, but percentile-relative
        gives the bandit a CONTINUOUS gradient instead of saturating."""
        percentile_fn = MagicMock(return_value=1500.0)
        shaper = RewardShaper(
            percentile_targets_fn=percentile_fn,
            niche_id="sports",
        )

        reward = shaper.compute_reward(
            platform="youtube",
            metrics={"views": 1000.0},
        )

        # 1000 / 1500 ≈ 0.667 — but weights are present-only-rescaled to
        # 1.0 on views (the only metric we passed). Either way the
        # reward should reflect the 0.667 normalisation, not the
        # 1.0-saturated hardcoded path.
        assert reward < 1.0
        assert reward == pytest.approx(0.667, abs=0.01)
        percentile_fn.assert_called()  # confirm the fn was actually consulted

    def test_falls_back_to_hardcoded_when_fn_returns_none(self):
        """Cold-start: insufficient data → fn returns None → use hardcoded.
        Pre-fix behavior preserved during warm-up phase."""
        percentile_fn = MagicMock(return_value=None)
        shaper = RewardShaper(
            percentile_targets_fn=percentile_fn,
            niche_id="ai_creators",
        )

        # views=1000 with hardcoded yt target=200 → normalised=1.0 (capped)
        reward = shaper.compute_reward(
            platform="youtube",
            metrics={"views": 1000.0},
        )
        assert reward == 1.0  # saturated under fallback

    def test_falls_back_when_fn_raises(self):
        """Defensive: fn exceptions must not crash reward computation."""
        percentile_fn = MagicMock(side_effect=RuntimeError("db down"))
        shaper = RewardShaper(
            percentile_targets_fn=percentile_fn,
            niche_id="gaming",
        )

        # Should not raise — should return some valid reward via fallback path
        reward = shaper.compute_reward(
            platform="youtube",
            metrics={"views": 100.0},
        )
        assert 0.0 <= reward <= 1.0

    def test_no_fn_means_legacy_behaviour_unchanged(self):
        """No percentile_targets_fn injected → reward identical to pre-fix.
        Pin so callers can adopt this incrementally without breaking."""
        shaper = RewardShaper()  # no fn

        reward = shaper.compute_reward(
            platform="youtube",
            metrics={"views": 100.0},
        )
        # views=100, hardcoded target=200 → normalised=0.5
        # views is the only metric → rescaled weight = 1.0
        # reward = 1.0 * 0.5 = 0.5
        assert reward == pytest.approx(0.5)
