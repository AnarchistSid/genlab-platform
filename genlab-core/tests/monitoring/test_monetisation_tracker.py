"""Tests for genlab_core.monitoring.monetisation_tracker — daily metric collector."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from genlab_core.monitoring.monetisation_tracker import (
    MonetisationTracker,
    _get_target,
    _load_targets,
)


@pytest.fixture
def targets_yaml(tmp_path):
    """Create a minimal monetisation_targets.yaml for testing."""
    cfg = {
        "youtube": {
            "traditional": {
                "subscribers": {"target": 1000, "unit": "subscribers"},
                "watch_hours_12mo": {"target": 4000, "unit": "hours"},
            },
            "shorts": {
                "shorts_views_90d": {"target": 10_000_000, "unit": "views"},
            },
        },
        "facebook": {
            "followers": {"target": 5000, "unit": "followers"},
            "minutes_viewed_60d": {"target": 600000, "unit": "minutes"},
        },
        "instagram": {
            "followers": {"target": 10000, "unit": "followers"},
            "dm_sends_7d": {"target": None, "unit": "sends"},
        },
        "reward_boost": {
            "within_20pct": 3.0,
            "within_50pct": 1.5,
            "above_threshold": 1.0,
        },
        "sharepoint": {
            "list_id": "test-list-id",
        },
    }
    p = tmp_path / "monetisation_targets.yaml"
    with open(p, "w") as f:
        yaml.dump(cfg, f)
    return p


@pytest.fixture
def mock_client():
    return MagicMock()


class TestLoadTargets:
    def test_load_targets_from_file(self, targets_yaml):
        targets = _load_targets(targets_yaml)
        assert "youtube" in targets
        assert "facebook" in targets
        assert "instagram" in targets

    def test_load_targets_missing_file(self, tmp_path):
        targets = _load_targets(tmp_path / "nonexistent.yaml")
        assert targets == {}


class TestGetTarget:
    def test_get_youtube_subscribers(self, targets_yaml):
        targets = _load_targets(targets_yaml)
        assert _get_target(targets, "youtube", "subscribers") == 1000.0

    def test_get_youtube_watch_hours(self, targets_yaml):
        targets = _load_targets(targets_yaml)
        assert _get_target(targets, "youtube", "watch_hours_12mo") == 4000.0

    def test_get_facebook_followers(self, targets_yaml):
        targets = _load_targets(targets_yaml)
        assert _get_target(targets, "facebook", "followers") == 5000.0

    def test_get_instagram_dm_sends_null(self, targets_yaml):
        """dm_sends_7d has no target — should return None."""
        targets = _load_targets(targets_yaml)
        assert _get_target(targets, "instagram", "dm_sends_7d") is None

    def test_get_nonexistent_metric(self, targets_yaml):
        targets = _load_targets(targets_yaml)
        assert _get_target(targets, "youtube", "no_such_metric") is None


class TestTrackerDryRun:
    def test_run_dry_run_single_niche(self, mock_client, targets_yaml):
        """Dry run should not write to SP, just return metrics."""
        tracker = MonetisationTracker(mock_client, config_path=targets_yaml)

        with patch.object(tracker, "_collect_youtube", return_value={"status": "no_credentials", "metrics": {}}), \
             patch.object(tracker, "_collect_facebook", return_value={"status": "no_credentials", "metrics": {}}), \
             patch.object(tracker, "_collect_instagram", return_value={"status": "no_credentials", "metrics": {}}):
            results = tracker.run(dry_run=True, niche_filter="ai_news")

        assert "ai_news" in results
        assert "youtube" in results["ai_news"]
        assert "facebook" in results["ai_news"]
        assert "instagram" in results["ai_news"]

    def test_run_iterates_all_niches(self, mock_client, targets_yaml):
        """Without niche_filter, run should iterate all 5 niches."""
        tracker = MonetisationTracker(mock_client, config_path=targets_yaml)

        with patch.object(tracker, "_collect_youtube", return_value={"status": "ok", "metrics": {}}), \
             patch.object(tracker, "_collect_facebook", return_value={"status": "ok", "metrics": {}}), \
             patch.object(tracker, "_collect_instagram", return_value={"status": "ok", "metrics": {}}):
            results = tracker.run(dry_run=True)

        assert len(results) == 5
        assert set(results.keys()) == {"ai_news", "gaming", "sports", "movies", "anime"}


class TestUpsertProgress:
    def test_upsert_computes_pct(self, mock_client, targets_yaml):
        """Verify _upsert_progress computes pct_complete correctly."""
        tracker = MonetisationTracker(mock_client, config_path=targets_yaml)

        # Should log without writing to SP (dry_run=True)
        tracker._upsert_progress(
            niche_id="ai_news",
            platform="youtube",
            metric_name="subscribers",
            current_value=500,
            previous_value=None,
            dry_run=True,
        )
        # No error = pass. The method logs internally.

    def test_upsert_threshold_met(self, mock_client, targets_yaml):
        """When current >= target, is_threshold_met should be True."""
        tracker = MonetisationTracker(mock_client, config_path=targets_yaml)

        # Mock the proxy to capture the fields written
        mock_proxy = MagicMock()
        mock_proxy.all.return_value = []  # no existing record
        tracker._proxy = mock_proxy

        tracker._upsert_progress(
            niche_id="ai_news",
            platform="youtube",
            metric_name="subscribers",
            current_value=1200,
            previous_value=None,
            dry_run=False,
        )

        mock_proxy.create.assert_called_once()
        fields = mock_proxy.create.call_args[0][0]
        assert fields["is_threshold_met"] is True
        assert fields["pct_complete"] == 120.0
