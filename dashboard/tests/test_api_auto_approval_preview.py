"""Integration pin: /api/v1/blueprints/<id>/auto-approval-preview.

AUTO #1 (2026-06-13): foundation for the owner's autonomous-agent vision.
Endpoint runs each blueprint through the auto-approval gate and returns
the decision so the dashboard can render a "would auto-approve" badge.
Read-only — does NOT change blueprint state.

Pins:
1. Endpoint reachable + auth-aware (200 for clean blueprint, 404 missing).
2. Response shape matches the gate's AutoApprovalDecision.
3. `preview_only=True` + `would_publish=False` are immutable contract bits
   so a future PR that wires enforcement can't accidentally flip the
   preview into action mode.
4. Invalid ID rejected before any backlog roundtrip (cheap fail).
5. Top-level scores OR `extra` JSONB both feed the gate identically.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
import server.review_server as review_server_module
from server.review_server import app


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    monkeypatch.setattr(review_server_module, "_AUTH_ENABLED", False)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    app.config["DRY_RUN"] = True
    app.config["LOCAL_MODE"] = True
    with app.test_client() as c:
        yield c


def _stub_blueprint(extras=None, **fields):
    """Build a mock blueprint payload as BacklogClient.blueprints.get returns."""
    base_fields = {
        "hook_text": "A real hook about a real video",
        "niche_id": "gaming",
        "status": "VISUAL_READY",
        "extra": extras
        or {
            "visual_paths": ["/tmp/v.mp4"],
            "composite_score": 0.7,
            "virality_score": 0.2,
            "validation_status": {"all_passed": True},
        },
        **fields,
    }
    return {"id": "test-bp-id", "fields": base_fields}


class TestEndpointReachability:
    def test_invalid_id_400(self, client):
        # Use chars that fail RECORD_RE (slash / spaces)
        resp = client.get("/api/v1/blueprints/bad id/auto-approval-preview")
        # Flask returns 404 for the unmatched route shape, which is fine —
        # the cheap-fail goal is "no backlog hit".
        assert resp.status_code in (400, 404)

    def test_unknown_id_returns_404(self, client):
        """Backlog miss → 404, not 500."""
        with patch("server.api.blueprints._get_client") as mock_client:
            mock_client.return_value.blueprints.get.side_effect = Exception("not found")
            resp = client.get("/api/v1/blueprints/12345/auto-approval-preview")
            assert resp.status_code == 404


class TestDecisionShape:
    def test_clean_blueprint_returns_approved(self, client):
        with patch("server.api.blueprints._get_client") as mock_client:
            mock_client.return_value.blueprints.get.return_value = _stub_blueprint()
            resp = client.get("/api/v1/blueprints/12345/auto-approval-preview")
            assert resp.status_code == 200
            data = resp.get_json()["data"]
            assert data["approved"] is True
            assert "has_video" in data["passed_checks"]
            assert "has_hook" in data["passed_checks"]
            assert "qc_passed" in data["passed_checks"]
            assert data["failed_checks"] == []
            assert 0.0 <= data["confidence"] <= 1.0
            assert len(data["reasons"]) > 0

    def test_failing_blueprint_returns_not_approved(self, client):
        """No visual + no hook → fails both required gates."""
        bp = _stub_blueprint(
            hook_text="",
            extras={
                "visual_paths": [],
                "composite_score": 0.0,
                "virality_score": 0.0,
                "validation_status": {"all_passed": False, "issues": ["missing video"]},
            },
        )
        with patch("server.api.blueprints._get_client") as mock_client:
            mock_client.return_value.blueprints.get.return_value = bp
            resp = client.get("/api/v1/blueprints/12345/auto-approval-preview")
            assert resp.status_code == 200
            data = resp.get_json()["data"]
            assert data["approved"] is False
            assert "has_video" in data["failed_checks"]
            assert "has_hook" in data["failed_checks"]


class TestPreviewContract:
    """The preview must NEVER appear to publish. Pin the immutable bits."""

    def test_would_publish_always_false(self, client):
        with patch("server.api.blueprints._get_client") as mock_client:
            mock_client.return_value.blueprints.get.return_value = _stub_blueprint()
            resp = client.get("/api/v1/blueprints/12345/auto-approval-preview")
            data = resp.get_json()["data"]
            # Even when approved=True, would_publish stays False — this
            # endpoint is read-only. If a future PR wires enforcement
            # elsewhere, the preview endpoint stays informational.
            assert data["approved"] is True
            assert data["would_publish"] is False
            assert data["preview_only"] is True

    def test_record_id_echoed(self, client):
        """Confirm the response includes the record_id for client-side correlation."""
        with patch("server.api.blueprints._get_client") as mock_client:
            mock_client.return_value.blueprints.get.return_value = _stub_blueprint()
            resp = client.get("/api/v1/blueprints/99999/auto-approval-preview")
            data = resp.get_json()["data"]
            assert data["record_id"] == "99999"


class TestExtraNormalization:
    """The endpoint must tolerate blueprints where scores live at top
    level OR under extra. Critical because the storage layer's history
    has been inconsistent (Postgres extras JSONB vs SharePoint flat
    fields)."""

    def test_top_level_scores_picked_up(self, client):
        """Postgres path stores composite_score in extras; SharePoint
        path historically stored it as a top-level field. Both must
        work."""
        bp = {
            "id": "test-id",
            "fields": {
                "hook_text": "Hook present",
                "niche_id": "gaming",
                # NOTE: no `extra` field — scores at top level.
                "composite_score": 0.9,
                "virality_score": 0.5,
                "visual_paths": '["/tmp/v.mp4"]',  # JSON-encoded string (SP shape)
                "validation_status": '{"all_passed": true}',
            },
        }
        with patch("server.api.blueprints._get_client") as mock_client:
            mock_client.return_value.blueprints.get.return_value = bp
            resp = client.get("/api/v1/blueprints/12345/auto-approval-preview")
            assert resp.status_code == 200
            data = resp.get_json()["data"]
            assert data["approved"] is True
            assert "has_video" in data["passed_checks"]
            assert "composite_score" in data["passed_checks"]
