"""Tests for /api/v1/media-kit/<niche_id> endpoint.

The endpoint is decoration over the existing fetch_progress reader +
the sponsorship_readiness tier-computation helpers. Tests focus on:
  - audience summary shape (headline metrics only, sorted by size)
  - tier passes through from PR #481's logic unchanged
  - 404 on invalid niche_id
  - empty data → valid empty kit (cold-start path)
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
def _reset_sponsorship_cache(monkeypatch):
    """sponsorship_readiness module caches for 60s; the media kit's
    tier computation calls into those helpers. Reset between tests so
    each test sees its mock data, not the prior test's."""
    from server.api import sponsorship_readiness as mod

    mod._cache.clear()
    mod._cache.update({"data": None, "ts": 0.0})
    yield


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


_AI_CREATORS_FULL = [
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
    {
        "niche_id": "ai_creators",
        "platform": "instagram",
        "metric_name": "followers",
        "current_value": 350,
        "target_value": 10000,
        "pct_complete": 3.5,
        "delta_7d": 15,
        "days_to_threshold_est": 600,
        "is_threshold_met": False,
    },
]


class TestHappyPath:
    @patch(
        "server.api.media_kit._pg_fetch_progress",
        return_value=_AI_CREATORS_FULL,
    )
    def test_returns_200_with_niche_payload(self, _mock, client):
        """Pin: valid niche_id + populated data → 200 with kit payload."""
        resp = client.get("/api/v1/media-kit/ai_creators")
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert data["niche_id"] == "ai_creators"

    @patch(
        "server.api.media_kit._pg_fetch_progress",
        return_value=_AI_CREATORS_FULL,
    )
    def test_audience_surfaces_headline_metric_per_platform(self, _mock, client):
        """Pin: per-platform audience surfaces ONLY the headline metric
        (subscribers / followers / fans). Internal monetisation
        metrics like watch_hours_12mo are intentionally NOT in the
        kit — brands don't pitch against watch-hours."""
        resp = client.get("/api/v1/media-kit/ai_creators")
        data = json.loads(resp.data)["data"]
        audience = data["audience"]
        # YouTube: surfaces "subscribers" (not "watch_hours_12mo")
        yt = next(p for p in audience if p["platform"] == "youtube")
        assert yt["metric_name"] == "subscribers"
        assert yt["current_value"] == 1200
        # Instagram: surfaces "followers"
        ig = next(p for p in audience if p["platform"] == "instagram")
        assert ig["metric_name"] == "followers"
        assert ig["current_value"] == 350
        # ONLY 2 platforms returned despite 3 metric rows (watch_hours
        # collapses into the subscribers headline)
        assert {p["platform"] for p in audience} == {"youtube", "instagram"}

    @patch(
        "server.api.media_kit._pg_fetch_progress",
        return_value=_AI_CREATORS_FULL,
    )
    def test_audience_sorted_by_descending_follower_count(self, _mock, client):
        """Pin: kit's audience LIST reads 'strongest first'. YouTube
        (1200 subs) before Instagram (350 followers) — operator-friendly
        ordering when copy-pasting into outreach emails. The list shape
        (vs a dict) is deliberate: it survives Flask's JSON_SORT_KEYS
        re-ordering."""
        resp = client.get("/api/v1/media-kit/ai_creators")
        data = json.loads(resp.data)["data"]
        platform_order = [p["platform"] for p in data["audience"]]
        assert platform_order == ["youtube", "instagram"]

    @patch(
        "server.api.media_kit._pg_fetch_progress",
        return_value=_AI_CREATORS_FULL,
    )
    def test_tier_passes_through_from_sponsorship_logic(self, _mock, client):
        """Pin: tier value comes from sponsorship_readiness._compute_tier
        unchanged. Kit and Mission Control card MUST NEVER disagree on
        tier — they share the computation helper by import."""
        resp = client.get("/api/v1/media-kit/ai_creators")
        data = json.loads(resp.data)["data"]
        # YouTube has all metrics met → eligible_now
        assert data["tier"] == "eligible_now"
        assert data["nearest_threshold_days"] == 0

    @patch(
        "server.api.media_kit._pg_fetch_progress",
        return_value=_AI_CREATORS_FULL,
    )
    def test_monetised_platforms_list_present(self, _mock, client):
        """Pin: monetised_platforms is a sorted list of platforms with
        every metric met. YouTube qualifies (both subs and watch_hours
        met); Instagram doesn't (one metric, not met)."""
        resp = client.get("/api/v1/media-kit/ai_creators")
        data = json.loads(resp.data)["data"]
        assert data["monetised_platforms"] == ["youtube"]

    @patch(
        "server.api.media_kit._pg_fetch_progress",
        return_value=_AI_CREATORS_FULL,
    )
    def test_generated_at_iso_8601_utc(self, _mock, client):
        """Pin: generated_at is an ISO-8601 UTC timestamp. Frontend
        renders it as the kit's 'data as-of' line; missing tz suffix
        would localise incorrectly across operator timezones."""
        resp = client.get("/api/v1/media-kit/ai_creators")
        data = json.loads(resp.data)["data"]
        gen = data["generated_at"]
        # Either +00:00 or Z suffix is acceptable; Python's isoformat()
        # produces +00:00 from a UTC datetime.
        assert gen.endswith("+00:00") or gen.endswith("Z")


class TestNicheValidation:
    def test_unknown_niche_id_404(self, client):
        """Pin: niche_id not in the closed 5 → 404. Invalid input
        deserves a clear error (vs the cold-start empty-data case
        which is valid state and returns 200)."""
        resp = client.get("/api/v1/media-kit/not_a_real_niche")
        assert resp.status_code == 404
        body = json.loads(resp.data)
        # Error body lists the valid options to help the caller debug
        assert "not_a_real_niche" in body.get("error", "")

    @pytest.mark.parametrize(
        "niche_id",
        ["ai_creators", "gaming", "sports", "movies", "anime"],
    )
    def test_each_known_niche_accepted(self, niche_id, client):
        """Pin: all 5 canonical niche_ids are accepted (no typos)."""
        with patch(
            "server.api.media_kit._pg_fetch_progress",
            return_value=[],
        ):
            resp = client.get(f"/api/v1/media-kit/{niche_id}")
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert data["niche_id"] == niche_id


class TestColdStart:
    @patch(
        "server.api.media_kit._pg_fetch_progress",
        return_value=[],
    )
    def test_empty_data_returns_valid_shell(self, _mock, client):
        """Pin: zero rows for a valid niche → 200 with shell payload.
        Frontend page renders 'data pending' placeholders rather than
        a 404 (which would imply the niche is invalid)."""
        resp = client.get("/api/v1/media-kit/gaming")
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert data["niche_id"] == "gaming"
        assert data["audience"] == []
        assert data["monetised_platforms"] == []
        assert data["tier"] == "tracking"
        assert data["nearest_threshold_days"] is None


class TestFailure:
    @patch(
        "server.api.media_kit._pg_fetch_progress",
        side_effect=RuntimeError("pg connection refused"),
    )
    def test_fetch_exception_returns_500(self, _mock, client):
        """Pin: infra-layer exception → 500 with error message.
        The page can render an error state; silent-200-with-empty-data
        would hide the bug."""
        resp = client.get("/api/v1/media-kit/ai_creators")
        assert resp.status_code == 500
