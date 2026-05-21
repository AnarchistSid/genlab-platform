"""Tests for intelligence_hub pure functions.

Tests the read-only intelligence layer: trend boost, optimal times,
viral alerts, content overlap, baselines. All file I/O is mocked.
"""

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

# Run via: uv run --package genlab-core python -m pytest scripts/tests/test_intelligence_hub.py

# Ensure scripts/ is on path so intelligence_hub can be imported directly
SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import intelligence_hub as hub

# ── _read_json ──────────────────────────────────────────────


class TestReadJson:
    def test_returns_none_for_missing_file(self, tmp_path):
        assert hub._read_json(tmp_path / "nope.json") is None

    def test_returns_none_for_corrupt_json(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{broken")
        assert hub._read_json(bad) is None

    def test_reads_valid_json(self, tmp_path):
        good = tmp_path / "good.json"
        good.write_text('{"key": "value"}')
        assert hub._read_json(good) == {"key": "value"}


# ── Trend Boost ──────────────────────────────────────────────


class TestGetTrendBoost:
    def test_returns_zero_when_no_cache(self):
        with patch.object(hub, "TRENDS_FILE", Path("/tmp/nonexistent_trends.json")):
            assert hub.get_trend_boost("anything") == 0.0

    def test_returns_80_for_daily_trending_match(self, tmp_path):
        cache = tmp_path / "trends.json"
        cache.write_text(
            json.dumps(
                {
                    "generated_at": "2026-03-13T10:00:00Z",
                    "daily_trending": [
                        {"query": "Elden Ring DLC"},
                        {"query": "GTA VI"},
                    ],
                }
            )
        )
        with patch.object(hub, "TRENDS_FILE", cache):
            assert hub.get_trend_boost("Elden Ring DLC") == 80.0

    def test_returns_zero_for_no_match(self, tmp_path):
        cache = tmp_path / "trends.json"
        cache.write_text(
            json.dumps(
                {
                    "daily_trending": [{"query": "GTA VI"}],
                }
            )
        )
        with patch.object(hub, "TRENDS_FILE", cache):
            assert hub.get_trend_boost("Minecraft") == 0.0

    def test_niche_specific_boost(self, tmp_path):
        cache = tmp_path / "trends.json"
        cache.write_text(
            json.dumps(
                {
                    "daily_trending": [],
                    "niches": {
                        "gaming": {
                            "trending_topics": [
                                {"keyword": "valorant", "score": 60, "direction": "rising"},
                            ],
                        },
                    },
                }
            )
        )
        with patch.object(hub, "TRENDS_FILE", cache):
            score = hub.get_trend_boost("Valorant Update", niche_id="gaming")
            assert score == 80.0  # 60 + 20 rising bonus

    def test_falling_direction_reduces_score(self, tmp_path):
        cache = tmp_path / "trends.json"
        cache.write_text(
            json.dumps(
                {
                    "daily_trending": [],
                    "niches": {
                        "gaming": {
                            "trending_topics": [
                                {"keyword": "fortnite", "score": 50, "direction": "falling"},
                            ],
                        },
                    },
                }
            )
        )
        with patch.object(hub, "TRENDS_FILE", cache):
            score = hub.get_trend_boost("Fortnite", niche_id="gaming")
            assert score == 35.0  # 50 - 15 falling penalty


# ── Optimal Time ─────────────────────────────────────────────


class TestGetOptimalTime:
    def test_returns_none_when_no_cache(self):
        with patch.object(hub, "SCHEDULE_FILE", Path("/tmp/nonexistent_schedule.json")):
            assert hub.get_optimal_time("instagram") is None

    def test_returns_platform_best_hour(self, tmp_path):
        cache = tmp_path / "schedule.json"
        cache.write_text(
            json.dumps(
                {
                    "platforms": {
                        "instagram": {
                            "best_hours_ist": [{"hour": "12:00"}, {"hour": "20:00"}],
                        },
                    },
                }
            )
        )
        with patch.object(hub, "SCHEDULE_FILE", cache):
            assert hub.get_optimal_time("instagram") == "12:00"

    def test_falls_back_to_cross_platform(self, tmp_path):
        cache = tmp_path / "schedule.json"
        cache.write_text(
            json.dumps(
                {
                    "platforms": {},
                    "recommended_posting_time_ist": "18:30",
                }
            )
        )
        with patch.object(hub, "SCHEDULE_FILE", cache):
            assert hub.get_optimal_time("youtube") == "18:30"


# ── Viral Alerts ─────────────────────────────────────────────


class TestGetRecentViralAlerts:
    def test_returns_empty_when_no_cache(self):
        with patch.object(hub, "VIRAL_FILE", Path("/tmp/nonexistent_viral.json")):
            assert hub.get_recent_viral_alerts() == []

    def test_filters_old_alerts(self, tmp_path):
        now = datetime.now(UTC)
        old = (now - timedelta(hours=72)).isoformat()
        recent = (now - timedelta(hours=6)).isoformat()
        cache = tmp_path / "viral.json"
        cache.write_text(
            json.dumps(
                [
                    {"detected_at": old, "title": "old alert"},
                    {"detected_at": recent, "title": "recent alert"},
                ]
            )
        )
        with patch.object(hub, "VIRAL_FILE", cache):
            alerts = hub.get_recent_viral_alerts(hours=48)
        assert len(alerts) == 1
        assert alerts[0]["title"] == "recent alert"


# ── Baselines ────────────────────────────────────────────────


class TestGetBaselines:
    def test_returns_empty_when_no_cache(self):
        with patch.object(hub, "BASELINES_FILE", Path("/tmp/nonexistent_baselines.json")):
            assert hub.get_baselines() == {}

    def test_reads_flat_baselines(self, tmp_path):
        cache = tmp_path / "baselines.json"
        # Flat format (legacy): no nested dicts
        cache.write_text(
            json.dumps(
                {
                    "instagram": 500.0,
                    "youtube": 1200.0,
                }
            )
        )
        with patch.object(hub, "BASELINES_FILE", cache):
            data = hub.get_baselines()
        assert "instagram" in data
        assert data["instagram"] == 500.0

    def test_reads_niche_scoped_baselines(self, tmp_path):
        cache = tmp_path / "baselines.json"
        cache.write_text(
            json.dumps(
                {
                    "gaming": {"instagram": 300.0, "youtube": 800.0},
                    "_global": {"instagram": 500.0},
                }
            )
        )
        with patch.object(hub, "BASELINES_FILE", cache):
            data = hub.get_baselines(niche_id="gaming")
        assert data["instagram"] == 300.0


# ── Known Niches ─────────────────────────────────────────────


class TestKnownNiches:
    def test_all_five_niches_registered(self):
        expected = {"ai_creators", "gaming", "sports", "movies", "anime"}
        assert set(hub.KNOWN_NICHES.keys()) == expected

    def test_all_six_platforms_registered(self):
        expected = {"youtube", "instagram", "facebook", "x_twitter", "threads", "tiktok"}
        assert set(hub.KNOWN_PLATFORMS) == expected
