"""Pin the 2026-08-15 short-window follower_delta wire.

## What broke pre-fix

`metric_collector.fetch_platform_metrics` only called
`augment_metrics_with_follower_delta` when window=='168h'. The auto-
approver's outcome_readiness reads reward_48h exclusively — meaning
the auto-approver's confidence loop was BLIND to follower signal
even though the reward weight for `follower_gained` was declared at
0.15 (IG/FB) and `subscriber_gained` at 0.2 (YT).

Practical effect: a gate-approved post that got 50 views and 5 new
followers scored identically to one that got 50 views and 0 new
followers, because the auto-approver's 48h reward window never saw
the follower delta. Gate learned to approve engagement, not growth.

## Fix contract (this test locks it)

`fetch_platform_metrics` now calls augment for windows in
{"24h", "48h", "168h"} with proportional lookback:

    window | lookback_days
    24h    | 1
    48h    | 2
    168h   | 7

Windows outside this set (unknown, future additions) remain
un-augmented until explicitly added — matches the original
conservative default.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch


def _stub_platform_fetcher(monkeypatch, platform: str, base_metrics: dict) -> None:
    """Replace the platform fetcher registry entry with a stub that
    returns known metrics, so tests can isolate the augment behavior."""
    from genlab_core.learning import metric_collector as mc

    def _fake_fetch(_raw_id, niche_id=""):
        return dict(base_metrics)

    monkeypatch.setattr(mc, f"_fetch_{platform}", _fake_fetch, raising=False)


class TestShortWindowAugment:
    def test_48h_window_populates_follower_gained(self, monkeypatch):
        from genlab_core.learning.metric_collector import fetch_platform_metrics

        called_with = {}

        def _fake_get_delta(
            niche_id, platform, publish_time, *, window_days, metric_name,
        ):
            called_with["window_days"] = window_days
            called_with["platform"] = platform
            called_with["metric_name"] = metric_name
            return 2

        _stub_platform_fetcher(monkeypatch, "instagram", {"views": 100})
        with patch(
            "genlab_core.learning.metrics.follower_delta.get_follower_delta",
            side_effect=_fake_get_delta,
        ):
            metrics = fetch_platform_metrics(
                "instagram", "test_post_id", "48h", niche_id="gaming",
            )
        assert metrics.get("follower_gained") == 2, (
            "48h window MUST populate follower_gained — auto-approver "
            "reads 48h reward, was blind to follower signal pre-fix"
        )
        assert called_with["window_days"] == 2, (
            "48h window uses 2-day lookback (matches roughly)"
        )
        assert called_with["metric_name"] == "followers"

    def test_24h_window_populates_follower_gained(self, monkeypatch):
        from genlab_core.learning.metric_collector import fetch_platform_metrics

        called_with = {}

        def _fake_get_delta(
            niche_id, platform, publish_time, *, window_days, metric_name,
        ):
            called_with["window_days"] = window_days
            return 1

        _stub_platform_fetcher(monkeypatch, "facebook", {"views": 50})
        with patch(
            "genlab_core.learning.metrics.follower_delta.get_follower_delta",
            side_effect=_fake_get_delta,
        ):
            metrics = fetch_platform_metrics(
                "facebook", "test_post_id", "24h", niche_id="movies",
            )
        assert metrics.get("follower_gained") == 1
        assert called_with["window_days"] == 1

    def test_168h_still_uses_7day_lookback(self, monkeypatch):
        """Regression pin — the 2026-07-17 168h wire must still work
        with 7-day lookback."""
        from genlab_core.learning.metric_collector import fetch_platform_metrics

        called_with = {}

        def _fake_get_delta(
            niche_id, platform, publish_time, *, window_days, metric_name,
        ):
            called_with["window_days"] = window_days
            return 7

        _stub_platform_fetcher(monkeypatch, "instagram", {"views": 500})
        with patch(
            "genlab_core.learning.metrics.follower_delta.get_follower_delta",
            side_effect=_fake_get_delta,
        ):
            metrics = fetch_platform_metrics(
                "instagram", "test_post_id", "168h", niche_id="ai_creators",
            )
        assert metrics.get("follower_gained") == 7
        assert called_with["window_days"] == 7

    def test_youtube_writes_subscriber_gained_at_48h(self, monkeypatch):
        """YouTube uses subscriber_gained key at ALL windows, not just
        168h. Pin that the platform-specific key routing survives the
        short-window extension."""
        from genlab_core.learning.metric_collector import fetch_platform_metrics

        _stub_platform_fetcher(monkeypatch, "youtube", {"views": 12})
        with patch(
            "genlab_core.learning.metrics.follower_delta.get_follower_delta",
            return_value=3,
        ):
            metrics = fetch_platform_metrics(
                "youtube", "test_post_id", "48h", niche_id="ai_creators",
            )
        assert metrics.get("subscriber_gained") == 3
        assert "follower_gained" not in metrics

    def test_unknown_window_no_augment(self, monkeypatch):
        """Windows outside the {24h, 48h, 168h} set must NOT augment —
        conservative default. Any new window addition needs an explicit
        entry in the _WINDOW_LOOKBACK_DAYS map."""
        from genlab_core.learning.metric_collector import fetch_platform_metrics

        called = {"n": 0}

        def _spy(*a, **kw):
            called["n"] += 1
            return 5

        _stub_platform_fetcher(monkeypatch, "instagram", {"views": 100})
        with patch(
            "genlab_core.learning.metrics.follower_delta.get_follower_delta",
            side_effect=_spy,
        ):
            metrics = fetch_platform_metrics(
                "instagram", "test_post_id", "72h", niche_id="gaming",
            )
        assert called["n"] == 0
        assert "follower_gained" not in metrics
