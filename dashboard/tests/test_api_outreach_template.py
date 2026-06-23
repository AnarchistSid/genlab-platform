"""Tests for /api/v1/sponsorship/outreach-template?niche=<id>.

Endpoint generates a ready-to-send outreach template:
  - subject (tier-aware — "Sponsorship opportunity" for eligible_now,
    "Quick intro" for lower tiers so operator doesn't mislead recipient)
  - body (parameterised with audience stats + tier-appropriate CTA)
  - media_kit_url (relative URL for inclusion in the email body)

The template is intentionally OPERATOR-FILLABLE: brackets for [BRAND]
and [NAME], everything else pre-drafted. The operator pastes into
email/Slack/LinkedIn, edits 2 fields, hits send.

These pins lock in:

  Validation (3):
  - missing niche query param → 400
  - unknown niche_id → 404
  - all 5 canonical niches accepted

  Tier-aware copy (3):
  - eligible_now → subject "Sponsorship opportunity: ..."
  - within_2_months → subject "Quick intro: ..." (not "opportunity")
  - tracking → CTA says "stay in touch", not "schedule a call"

  Body content (3):
  - body contains [BRAND] and [NAME] placeholders
  - body contains the media_kit_url
  - body contains at least one audience stat phrase

  Audience summary (1):
  - audience_summary returns top-3 platforms sorted by current_value

  Number formatting (2):
  - 1500 → "1.5K" (not "1500")
  - 1_500_000 → "1.5M"
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
def _reset_sponsorship_cache():
    """Reset sponsorship_readiness's in-process cache between tests so
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


# Eligible niche — YouTube fully met (both subs + watch_hours)
_ELIGIBLE_NOW = [
    {
        "niche_id": "ai_creators",
        "platform": "youtube",
        "metric_name": "subscribers",
        "current_value": 1500,
        "target_value": 1000,
        "pct_complete": 150.0,
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

# Within-2-months niche — close + positive velocity
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

# Tracking — flat velocity
_TRACKING = [
    {
        "niche_id": "movies",
        "platform": "instagram",
        "metric_name": "followers",
        "current_value": 200,
        "target_value": 10000,
        "pct_complete": 2.0,
        "delta_7d": 0,
        "days_to_threshold_est": 600,
        "is_threshold_met": False,
    },
]


class TestValidation:
    def test_missing_niche_param_returns_400(self, client):
        """Pin: missing query param → 400 (not 500 or silent default).
        Forces the caller to pass an explicit niche."""
        resp = client.get("/api/v1/sponsorship/outreach-template")
        assert resp.status_code == 400

    def test_unknown_niche_returns_404(self, client):
        """Pin: niche not in the closed 5 → 404 with valid options
        in the error body."""
        resp = client.get("/api/v1/sponsorship/outreach-template?niche=not_a_real_niche")
        assert resp.status_code == 404
        body = json.loads(resp.data)
        assert "not_a_real_niche" in body.get("error", "")

    @pytest.mark.parametrize(
        "niche_id",
        ["ai_creators", "gaming", "sports", "movies", "anime"],
    )
    def test_each_known_niche_accepted(self, niche_id, client):
        """Pin: all 5 canonical niche IDs work (no typos in allowlist)."""
        with patch(
            "server.api.outreach_template._pg_fetch_progress",
            return_value=[],
        ):
            resp = client.get(f"/api/v1/sponsorship/outreach-template?niche={niche_id}")
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert data["niche_id"] == niche_id


class TestTierAwareCopy:
    @patch(
        "server.api.outreach_template._pg_fetch_progress",
        return_value=_ELIGIBLE_NOW,
    )
    def test_eligible_now_subject_says_opportunity(self, _mock, client):
        """Pin: eligible_now → subject 'Sponsorship opportunity'.
        Accurate framing — channel IS sponsor-ready."""
        resp = client.get("/api/v1/sponsorship/outreach-template?niche=ai_creators")
        data = json.loads(resp.data)["data"]
        assert data["tier"] == "eligible_now"
        assert "Sponsorship opportunity" in data["subject"]

    @patch(
        "server.api.outreach_template._pg_fetch_progress",
        return_value=_WITHIN_2_MONTHS,
    )
    def test_within_2_months_subject_says_quick_intro(self, _mock, client):
        """Pin: within_2_months → subject 'Quick intro' (NOT
        'opportunity'). Protects operator from accidentally promising
        a deal-ready audience to brand before it actually is."""
        resp = client.get("/api/v1/sponsorship/outreach-template?niche=gaming")
        data = json.loads(resp.data)["data"]
        assert data["tier"] == "within_2_months"
        assert "Quick intro" in data["subject"]
        assert "opportunity" not in data["subject"].lower()

    @patch(
        "server.api.outreach_template._pg_fetch_progress",
        return_value=_TRACKING,
    )
    def test_tracking_cta_says_stay_in_touch(self, _mock, client):
        """Pin: tracking tier → CTA says 'stay in touch' / 'put on your
        radar', NOT 'schedule a call'. Operator doesn't actively pitch
        before channel is sponsor-ready."""
        resp = client.get("/api/v1/sponsorship/outreach-template?niche=movies")
        data = json.loads(resp.data)["data"]
        assert data["tier"] == "tracking"
        body_lower = data["body"].lower()
        # CTA-shape check — soft outreach phrases
        assert "radar" in body_lower or "stay in touch" in body_lower
        # NOT the active-pitch phrase
        assert "open to brand partnerships" not in body_lower


class TestBodyContent:
    @patch(
        "server.api.outreach_template._pg_fetch_progress",
        return_value=_ELIGIBLE_NOW,
    )
    def test_body_contains_brand_and_name_placeholders(self, _mock, client):
        """Pin: body has [BRAND] + [NAME] placeholders for operator to
        fill. Without these the template can't be personalised — every
        send would say 'Hi BRAND'."""
        resp = client.get("/api/v1/sponsorship/outreach-template?niche=ai_creators")
        data = json.loads(resp.data)["data"]
        assert "[BRAND]" in data["body"]
        assert "[NAME]" in data["body"]

    @patch(
        "server.api.outreach_template._pg_fetch_progress",
        return_value=_ELIGIBLE_NOW,
    )
    def test_body_contains_media_kit_url(self, _mock, client):
        """Pin: body includes the media_kit_url so the recipient has
        a direct link to the full kit (not just numbers in email)."""
        resp = client.get("/api/v1/sponsorship/outreach-template?niche=ai_creators")
        data = json.loads(resp.data)["data"]
        assert data["media_kit_url"] == "/media-kit/ai_creators"
        assert "/media-kit/ai_creators" in data["body"]

    @patch(
        "server.api.outreach_template._pg_fetch_progress",
        return_value=_ELIGIBLE_NOW,
    )
    def test_body_contains_audience_stat(self, _mock, client):
        """Pin: body's stat block surfaces at least one audience number.
        Eligible_now ai_creators has 1500 YouTube subscribers → '1.5K'."""
        resp = client.get("/api/v1/sponsorship/outreach-template?niche=ai_creators")
        data = json.loads(resp.data)["data"]
        # Compact-format check (1500 → "1.5K", NOT "1500")
        assert "1.5K subscribers" in data["body"]


class TestAudienceSummary:
    @patch(
        "server.api.outreach_template._pg_fetch_progress",
        return_value=_ELIGIBLE_NOW,
    )
    def test_audience_summary_sorted_top_3(self, _mock, client):
        """Pin: audience_summary returns top-3 entries sorted DESC by
        current_value. Frontend uses this for the dashboard's inline
        preview pane next to the Copy button (no re-fetch)."""
        resp = client.get("/api/v1/sponsorship/outreach-template?niche=ai_creators")
        data = json.loads(resp.data)["data"]
        summary = data["audience_summary"]
        assert isinstance(summary, list)
        assert len(summary) <= 3
        # First entry has largest current_value
        if len(summary) >= 2:
            v0 = summary[0]["current_value"] or 0
            v1 = summary[1]["current_value"] or 0
            assert v0 >= v1


class TestNumberFormatting:
    def test_format_k(self):
        """Pin: thousand-scale numbers compact to 'Nk' format."""
        from server.api.outreach_template import _format_number

        assert _format_number(1500) == "1.5K"
        assert _format_number(9500) == "9.5K"

    def test_format_m(self):
        """Pin: million-scale numbers compact to 'NM' format."""
        from server.api.outreach_template import _format_number

        assert _format_number(1_500_000) == "1.5M"

    def test_format_small_and_none(self):
        """Pin: small numbers stay raw; None → em-dash."""
        from server.api.outreach_template import _format_number

        assert _format_number(42) == "42"
        assert _format_number(None) == "—"
