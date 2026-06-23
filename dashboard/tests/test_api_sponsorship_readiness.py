"""Tests for /api/v1/sponsorship/readiness endpoint.

Endpoint reuses the live monetisationprogress reader but adds:
  - per-niche tier classification (eligible_now / within_2_months /
    within_6_months / tracking)
  - per-platform primary-metric summary (closest-to-threshold OR
    most-over-threshold)
  - per-niche nearest_threshold_days projection

The tier logic + primary-metric selection are the new behavior — most
pins target those. The grouping reuses the existing endpoint's tested
shape so this file does NOT duplicate grouping-correctness pins.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import server.review_server as review_server_module
from server.review_server import app


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    monkeypatch.setattr(review_server_module, "_AUTH_ENABLED", False)


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    """The endpoint caches for 60s in-process. Reset between tests so
    each test sees the mock-fetched data, not the prior test's."""
    from server.api import sponsorship_readiness as mod

    mod._cache.clear()
    mod._cache.update({"data": None, "ts": 0.0})
    yield


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# A niche that has crossed every threshold on at least one platform.
# Mirrors a "monetised on YouTube" state — should yield tier=eligible_now.
_ELIGIBLE_NOW = [
    {
        "niche_id": "ai_creators",
        "platform": "youtube",
        "metric_name": "subscribers",
        "current_value": 1200,
        "target_value": 1000,
        "pct_complete": 120.0,
        "delta_7d": 30,
        "days_to_threshold_est": None,
        "is_threshold_met": True,
    },
    {
        "niche_id": "ai_creators",
        "platform": "youtube",
        "metric_name": "watch_hours_12mo",
        "current_value": 5000,
        "target_value": 4000,
        "pct_complete": 125.0,
        "delta_7d": 100,
        "days_to_threshold_est": None,
        "is_threshold_met": True,
    },
]

# Within 2 months — nearest unmet metric has days_to_threshold_est ≤ 60
# AND positive 7-day delta.
_WITHIN_2_MONTHS = [
    {
        "niche_id": "gaming",
        "platform": "instagram",
        "metric_name": "followers",
        "current_value": 9500,
        "target_value": 10000,
        "pct_complete": 95.0,
        "delta_7d": 50,
        "days_to_threshold_est": 21,
        "is_threshold_met": False,
    },
]

# Within 6 months — between 60 and 180 days
_WITHIN_6_MONTHS = [
    {
        "niche_id": "sports",
        "platform": "instagram",
        "metric_name": "followers",
        "current_value": 6000,
        "target_value": 10000,
        "pct_complete": 60.0,
        "delta_7d": 40,
        "days_to_threshold_est": 120,
        "is_threshold_met": False,
    },
]

# Tracking — far from threshold or flat/regressing velocity
_TRACKING_FLAT = [
    {
        "niche_id": "movies",
        "platform": "instagram",
        "metric_name": "followers",
        "current_value": 2000,
        "target_value": 10000,
        "pct_complete": 20.0,
        "delta_7d": 0,  # flat — no growth → no projectable ETA
        "days_to_threshold_est": 600,
        "is_threshold_met": False,
    },
]


class TestTierClassification:
    """The new behavior — these pins are the entire point of the PR."""

    @patch(
        "server.api.sponsorship_readiness._pg_fetch_progress",
        return_value=_ELIGIBLE_NOW,
    )
    def test_all_metrics_met_yields_eligible_now(self, _mock, client):
        """Pin: ANY platform with every metric met → tier=eligible_now."""
        resp = client.get("/api/v1/sponsorship/readiness")
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert data["ai_creators"]["tier"] == "eligible_now"
        assert data["ai_creators"]["nearest_threshold_days"] == 0

    @patch(
        "server.api.sponsorship_readiness._pg_fetch_progress",
        return_value=_WITHIN_2_MONTHS,
    )
    def test_close_metric_with_positive_velocity_yields_within_2_months(self, _mock, client):
        """Pin: unmet metric with days_to_threshold_est ≤ 60 AND
        positive delta_7d → within_2_months."""
        resp = client.get("/api/v1/sponsorship/readiness")
        data = json.loads(resp.data)["data"]
        assert data["gaming"]["tier"] == "within_2_months"
        assert data["gaming"]["nearest_threshold_days"] == 21

    @patch(
        "server.api.sponsorship_readiness._pg_fetch_progress",
        return_value=_WITHIN_6_MONTHS,
    )
    def test_mid_range_yields_within_6_months(self, _mock, client):
        """Pin: days_to_threshold_est between 60 and 180 → within_6_months."""
        resp = client.get("/api/v1/sponsorship/readiness")
        data = json.loads(resp.data)["data"]
        assert data["sports"]["tier"] == "within_6_months"
        assert data["sports"]["nearest_threshold_days"] == 120

    @patch(
        "server.api.sponsorship_readiness._pg_fetch_progress",
        return_value=_TRACKING_FLAT,
    )
    def test_flat_velocity_yields_tracking_even_with_eta(self, _mock, client):
        """Pin: zero or negative delta_7d → tracking regardless of ETA
        column value. ETA-from-momentary-uptick must not silently
        promote a flat niche to within_X_months."""
        resp = client.get("/api/v1/sponsorship/readiness")
        data = json.loads(resp.data)["data"]
        assert data["movies"]["tier"] == "tracking"
        assert data["movies"]["nearest_threshold_days"] is None


class TestPrimaryMetricSelection:
    @patch(
        "server.api.sponsorship_readiness._pg_fetch_progress",
        return_value=_ELIGIBLE_NOW,
    )
    def test_all_met_picks_highest_pct(self, _mock, client):
        """Pin: when ALL metrics are met, primary_metric picks the one
        with the highest pct_complete — operator-friendly headline."""
        resp = client.get("/api/v1/sponsorship/readiness")
        data = json.loads(resp.data)["data"]
        primary = data["ai_creators"]["platforms"]["youtube"]["primary_metric"]
        # watch_hours_12mo is 125% vs subscribers' 120% — picks the higher
        assert primary["metric_name"] == "watch_hours_12mo"
        assert primary["is_threshold_met"] is True

    def test_unmet_picks_closest_miss(self, client):
        """Pin: when ANY metric is unmet, primary_metric picks the
        closest unmet (highest pct_complete among unmet). Operator
        sees 'we're closest to X' not 'we're farthest from Y'."""
        rows = [
            {
                "niche_id": "anime",
                "platform": "youtube",
                "metric_name": "subscribers",
                "current_value": 200,
                "target_value": 1000,
                "pct_complete": 20.0,
                "delta_7d": 5,
                "days_to_threshold_est": 200,
                "is_threshold_met": False,
            },
            {
                "niche_id": "anime",
                "platform": "youtube",
                "metric_name": "watch_hours_12mo",
                "current_value": 3000,
                "target_value": 4000,
                "pct_complete": 75.0,
                "delta_7d": 30,
                "days_to_threshold_est": 90,
                "is_threshold_met": False,
            },
        ]
        with patch(
            "server.api.sponsorship_readiness._pg_fetch_progress",
            return_value=rows,
        ):
            resp = client.get("/api/v1/sponsorship/readiness")
        data = json.loads(resp.data)["data"]
        primary = data["anime"]["platforms"]["youtube"]["primary_metric"]
        # watch_hours_12mo at 75% beats subscribers at 20%
        assert primary["metric_name"] == "watch_hours_12mo"


class TestPlatformMonetisedFlag:
    def test_is_monetised_requires_all_metrics_met(self, client):
        """Pin: a platform is monetised ONLY when EVERY metric with a
        target is met. One unmet → not monetised. Mirrors the existing
        /monetisation/progress endpoint's logic exactly."""
        rows = [
            {
                "niche_id": "ai_creators",
                "platform": "youtube",
                "metric_name": "subscribers",
                "current_value": 1200,
                "target_value": 1000,
                "pct_complete": 120.0,
                "delta_7d": 30,
                "days_to_threshold_est": None,
                "is_threshold_met": True,
            },
            {
                "niche_id": "ai_creators",
                "platform": "youtube",
                "metric_name": "watch_hours_12mo",
                "current_value": 3500,
                "target_value": 4000,
                "pct_complete": 87.5,
                "delta_7d": 80,
                "days_to_threshold_est": 7,
                "is_threshold_met": False,
            },
        ]
        with patch(
            "server.api.sponsorship_readiness._pg_fetch_progress",
            return_value=rows,
        ):
            resp = client.get("/api/v1/sponsorship/readiness")
        data = json.loads(resp.data)["data"]
        platforms = data["ai_creators"]["platforms"]
        # One metric met, one unmet → NOT monetised
        assert platforms["youtube"]["is_monetised"] is False
        # ...and the tier reflects "close" not "eligible_now"
        assert data["ai_creators"]["tier"] == "within_2_months"


class TestFailSafe:
    @patch(
        "server.api.sponsorship_readiness._pg_fetch_progress",
        return_value=[],
    )
    def test_empty_data_returns_empty_dict_200(self, _mock, client):
        """Pin: no rows in the DB (cold start, DATABASE_URL unset,
        fresh prod box) → 200 + empty object. The card renders
        'no data yet' rather than crashing."""
        resp = client.get("/api/v1/sponsorship/readiness")
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert data == {}

    @patch(
        "server.api.sponsorship_readiness._pg_fetch_progress",
        side_effect=RuntimeError("pg connection refused"),
    )
    def test_fetch_exception_returns_500(self, _mock, client):
        """Pin: an exception inside the fetch DOES propagate to a 500
        (the dashboard treats this as 'something is broken in the
        infra layer' and the card shows a generic error). Compare
        against the silent-empty-list path which is the cold-start
        case — they need to be visibly different."""
        resp = client.get("/api/v1/sponsorship/readiness")
        assert resp.status_code == 500


class TestNicheFiltering:
    def test_unknown_niche_id_excluded(self, client):
        """Pin: rows where niche_id is missing/None get coerced to
        'unknown' by the existing reader; we filter them out so the
        response only contains real niches."""
        rows = [
            {
                "niche_id": None,
                "platform": "youtube",
                "metric_name": "subscribers",
                "current_value": 100,
                "target_value": 1000,
                "pct_complete": 10.0,
                "delta_7d": 1,
                "days_to_threshold_est": 999,
                "is_threshold_met": False,
            }
        ]
        with patch(
            "server.api.sponsorship_readiness._pg_fetch_progress",
            return_value=rows,
        ):
            resp = client.get("/api/v1/sponsorship/readiness")
        data = json.loads(resp.data)["data"]
        assert "unknown" not in data
