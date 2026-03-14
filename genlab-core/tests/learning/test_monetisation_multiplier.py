"""Tests for MonetisationMultiplierProvider in reward_shaper.py."""

from unittest.mock import patch

import pytest
import yaml

from genlab_core.learning.reward_shaper import MonetisationMultiplierProvider


@pytest.fixture
def targets_yaml(tmp_path):
    """Create monetisation_targets.yaml with boost tiers."""
    cfg = {
        "reward_boost": {
            "within_20pct": 3.0,
            "within_50pct": 1.5,
            "above_threshold": 1.0,
        },
        "sharepoint": {"list_id": "test-list-id"},
    }
    p = tmp_path / "monetisation_targets.yaml"
    with open(p, "w") as f:
        yaml.dump(cfg, f)
    return p


@pytest.fixture
def provider(targets_yaml):
    """Provider with pre-filled cache to avoid SP calls."""
    with patch(
        "genlab_core.learning.reward_shaper._TARGETS_PATH", targets_yaml
    ):
        p = MonetisationMultiplierProvider.__new__(MonetisationMultiplierProvider)
        p._client = None
        p._cache = {
            "ai_creators/youtube/subscribers": {
                "niche_id": "ai_creators",
                "platform": "youtube",
                "metric_name": "subscribers",
                "pct_complete": 85.0,
                "target_value": 1000,
                "is_threshold_met": False,
            },
            "ai_creators/youtube/watch_hours_12mo": {
                "niche_id": "ai_creators",
                "platform": "youtube",
                "metric_name": "watch_hours_12mo",
                "pct_complete": 45.0,
                "target_value": 4000,
                "is_threshold_met": False,
            },
            "gaming/instagram/followers": {
                "niche_id": "gaming",
                "platform": "instagram",
                "metric_name": "followers",
                "pct_complete": 110.0,
                "target_value": 10000,
                "is_threshold_met": True,
            },
        }
        p._cache_ts = 9999999999.0  # far future
        p._boost_tiers = {"within_20pct": 3.0, "within_50pct": 1.5, "above_threshold": 1.0}
        return p


class TestPctToMultiplier:
    def test_above_threshold(self, provider):
        assert provider._pct_to_multiplier(110) == 1.0

    def test_within_20pct(self, provider):
        assert provider._pct_to_multiplier(85) == 3.0

    def test_within_50pct(self, provider):
        assert provider._pct_to_multiplier(55) == 1.5

    def test_below_50pct(self, provider):
        assert provider._pct_to_multiplier(30) == 1.0


class TestGetMultiplier:
    def test_multiplier_within_20pct(self, provider):
        """ai_creators/youtube has max pct=85 → within_20pct → 3.0."""
        mult = provider.get_multiplier("ai_creators", "youtube")
        assert mult == 3.0

    def test_multiplier_above_threshold(self, provider):
        """gaming/instagram has pct=110 → above_threshold → 1.0."""
        mult = provider.get_multiplier("gaming", "instagram")
        assert mult == 1.0


class TestGetProgress:
    def test_get_existing_record(self, provider):
        rec = provider.get_progress("ai_creators", "youtube", "subscribers")
        assert rec is not None
        assert rec["pct_complete"] == 85.0

    def test_get_nonexistent_record(self, provider):
        rec = provider.get_progress("sports", "tiktok", "followers")
        assert rec is None

    def test_empty_cache_returns_default(self, provider):
        """Unknown niche/platform returns 1.0 multiplier."""
        mult = provider.get_multiplier("sports", "youtube")
        assert mult == 1.0
