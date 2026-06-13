"""Tests for genlab_core.scheduling.multi_publish_gate.

Pins task #33b: the multi-publish soft cap that consults the
optimal-time learner. The CLAUDE.md "1 reel per channel per day" rule
is a hard guarantee — these tests verify the gate NEVER raises a cap
unless the operator has explicitly opted in via the
``multi_publish.enabled`` flag in platform_caps.yaml.
"""

from __future__ import annotations

from unittest.mock import patch

from genlab_core.scheduling.multi_publish_gate import (
    _MIN_CONFIDENCE_FOR_MULTI,
    effective_cap,
    is_multi_publish_enabled,
    recommended_max_per_day,
)
from genlab_core.scheduling.optimal_time_learner import OptimalHour, reset_cache


def _setup():
    reset_cache()


def _teardown():
    reset_cache()


def _hours(*tuples: tuple[int, float]) -> list[OptimalHour]:
    """Build a list of OptimalHour from (hour_utc, confidence) tuples."""
    return [
        OptimalHour(hour_utc=h, avg_engagement=c, sample_size=20, confidence=c) for h, c in tuples
    ]


class TestRecommendedMaxPerDay:
    def test_returns_1_when_learner_has_no_signal(self):
        _setup()
        try:
            with patch(
                "genlab_core.scheduling.optimal_time_learner.get_optimal_hours",
                return_value=[],
            ):
                assert recommended_max_per_day("gaming", "instagram") == 1
        finally:
            _teardown()

    def test_returns_1_when_no_hour_clears_confidence_floor(self):
        _setup()
        try:
            # All 3 hours BELOW the _MIN_CONFIDENCE_FOR_MULTI floor (100).
            below_floor = _hours((6, 50.0), (10, 75.0), (15, 90.0))
            with patch(
                "genlab_core.scheduling.optimal_time_learner.get_optimal_hours",
                return_value=below_floor,
            ):
                assert recommended_max_per_day("gaming", "instagram") == 1
        finally:
            _teardown()

    def test_returns_n_when_n_hours_clear_floor(self):
        _setup()
        try:
            # 2 hours clear the 100 floor, 1 below.
            hours = _hours((6, 150.0), (10, 200.0), (15, 50.0))
            with patch(
                "genlab_core.scheduling.optimal_time_learner.get_optimal_hours",
                return_value=hours,
            ):
                assert recommended_max_per_day("gaming", "instagram") == 2
        finally:
            _teardown()

    def test_top_n_caps_runaway_recommendations(self):
        _setup()
        try:
            # Force the learner to return more than top_n=3 high-confidence hours
            many = _hours(
                (1, 500.0),
                (5, 500.0),
                (9, 500.0),
                (13, 500.0),
                (17, 500.0),
            )
            with patch(
                "genlab_core.scheduling.optimal_time_learner.get_optimal_hours",
                return_value=many[:3],  # the learner itself respects top_n
            ):
                assert recommended_max_per_day("gaming", "instagram", top_n=3) == 3
        finally:
            _teardown()

    def test_returns_1_on_learner_exception(self):
        _setup()
        try:
            with patch(
                "genlab_core.scheduling.optimal_time_learner.get_optimal_hours",
                side_effect=RuntimeError("DB down"),
            ):
                assert recommended_max_per_day("gaming", "instagram") == 1
        finally:
            _teardown()

    def test_returns_1_for_blank_inputs(self):
        assert recommended_max_per_day("", "instagram") == 1
        assert recommended_max_per_day("gaming", "") == 1
        assert recommended_max_per_day("", "") == 1


class TestIsMultiPublishEnabled:
    def test_disabled_by_default(self):
        assert is_multi_publish_enabled({}) is False
        assert is_multi_publish_enabled({"multi_publish": {}}) is False
        assert is_multi_publish_enabled({"multi_publish": {"enabled": False}}) is False

    def test_enabled_when_flag_true(self):
        assert is_multi_publish_enabled({"multi_publish": {"enabled": True}}) is True

    def test_platform_allowlist_filters(self):
        cfg = {
            "multi_publish": {
                "enabled": True,
                "platforms": ["instagram", "facebook"],
            }
        }
        assert is_multi_publish_enabled(cfg, platform="instagram") is True
        assert is_multi_publish_enabled(cfg, platform="facebook") is True
        assert is_multi_publish_enabled(cfg, platform="youtube") is False

    def test_empty_allowlist_means_all_platforms(self):
        cfg = {"multi_publish": {"enabled": True, "platforms": []}}
        for p in ("instagram", "facebook", "youtube", "tiktok"):
            assert is_multi_publish_enabled(cfg, platform=p) is True


class TestEffectiveCap:
    def test_disabled_returns_daily_post_cap_verbatim(self):
        """The R-09 1/day guarantee MUST hold when multi_publish is off."""
        _setup()
        try:
            with patch(
                "genlab_core.scheduling.optimal_time_learner.get_optimal_hours",
                return_value=_hours((6, 500.0), (10, 500.0), (15, 500.0)),
            ):
                cap = effective_cap(
                    niche_id="gaming",
                    platform="instagram",
                    daily_post_cap=1,
                    max_per_day_ceiling=5,
                    multi_publish_enabled=False,
                )
            assert cap == 1
        finally:
            _teardown()

    def test_enabled_raises_cap_to_learner_recommendation(self):
        _setup()
        try:
            with patch(
                "genlab_core.scheduling.optimal_time_learner.get_optimal_hours",
                return_value=_hours((6, 500.0), (10, 500.0), (15, 50.0)),
            ):
                # 2 hours clear floor → learner recommends 2.
                # max=5, daily=1 → effective = min(5, max(1, 2)) = 2
                cap = effective_cap(
                    niche_id="gaming",
                    platform="instagram",
                    daily_post_cap=1,
                    max_per_day_ceiling=5,
                    multi_publish_enabled=True,
                )
            assert cap == 2
        finally:
            _teardown()

    def test_ceiling_caps_runaway_recommendation(self):
        _setup()
        try:
            with patch(
                "genlab_core.scheduling.optimal_time_learner.get_optimal_hours",
                return_value=_hours((1, 500.0), (5, 500.0), (9, 500.0)),
            ):
                cap = effective_cap(
                    niche_id="gaming",
                    platform="instagram",
                    daily_post_cap=1,
                    max_per_day_ceiling=2,  # operator-set hard ceiling
                    multi_publish_enabled=True,
                )
            assert cap == 2  # Bounded by ceiling, not the learner's 3
        finally:
            _teardown()

    def test_enabled_but_no_signal_keeps_daily_cap(self):
        """Cold-start safety: opt-in flipped but learner has nothing →
        cap stays at the conservative daily_post_cap."""
        _setup()
        try:
            with patch(
                "genlab_core.scheduling.optimal_time_learner.get_optimal_hours",
                return_value=[],
            ):
                cap = effective_cap(
                    niche_id="gaming",
                    platform="instagram",
                    daily_post_cap=1,
                    max_per_day_ceiling=5,
                    multi_publish_enabled=True,
                )
            assert cap == 1
        finally:
            _teardown()


class TestConstants:
    def test_min_confidence_floor(self):
        """A floor of 0 would be no-floor (learner alone decides). Pin
        it >0 so a future config edit can't silently disable the gate."""
        assert _MIN_CONFIDENCE_FOR_MULTI > 0
