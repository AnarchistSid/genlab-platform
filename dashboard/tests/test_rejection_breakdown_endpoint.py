"""Pin: /api/v1/auto-approval/rejection-breakdown endpoint (Lever B).

Exposes the per-rejection-reason breakdown computed by
``calibration_logger.breakdown_by_category()``. Frontend contract pin
— the response shape MUST stay stable so the dashboard can render
rejection-reason histograms without per-request schema discovery.

These pins lock in:
  1. niche_id query param is required + whitelisted
  2. window_days defaults to 30, validated to 1..90
  3. Response includes per-category count + gate_agreed +
     gate_disagreed + computed disagreement_rate
  4. Empty list response when no data (cold-start safe)
  5. backend exception → 500 with error body (not crash)
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    from server import review_server as rs

    monkeypatch.setattr(rs, "_AUTH_ENABLED", False)


@pytest.fixture
def client():
    from flask import Flask
    from server.api.auto_approval import bp as auto_approval_bp

    app = Flask(__name__)
    app.register_blueprint(auto_approval_bp)
    return app.test_client()


class TestRejectionBreakdownEndpoint:
    def test_requires_niche_id_query_param(self, client):
        resp = client.get("/api/v1/auto-approval/rejection-breakdown")
        assert resp.status_code == 400
        body = resp.get_json()
        assert "niche_id" in (body.get("error") or "").lower()

    def test_rejects_unknown_niche_id(self, client):
        resp = client.get("/api/v1/auto-approval/rejection-breakdown?niche_id=fitness")
        assert resp.status_code == 400

    def test_validates_window_days_range(self, client):
        # 0 is invalid
        resp = client.get("/api/v1/auto-approval/rejection-breakdown?niche_id=gaming&window_days=0")
        assert resp.status_code == 400
        # 91 is invalid (max 90)
        resp = client.get(
            "/api/v1/auto-approval/rejection-breakdown?niche_id=gaming&window_days=91"
        )
        assert resp.status_code == 400
        # Non-int is invalid
        resp = client.get(
            "/api/v1/auto-approval/rejection-breakdown?niche_id=gaming&window_days=abc"
        )
        assert resp.status_code == 400

    def test_returns_breakdown_with_correct_shape(self, client):
        """When the backend returns a list of entries, the endpoint
        serializes them with all 5 expected fields per category."""
        from genlab_core.scheduling.calibration_logger import CategoryBreakdownEntry

        fake_entries = [
            CategoryBreakdownEntry(
                feedback_category="weak_hook",
                count=24,
                gate_agreed=4,
                gate_disagreed=20,
            ),
            CategoryBreakdownEntry(
                feedback_category="bad_fit",
                count=10,
                gate_agreed=8,
                gate_disagreed=2,
            ),
        ]

        with patch(
            "genlab_core.scheduling.calibration_logger.breakdown_by_category",
            return_value=fake_entries,
        ):
            resp = client.get(
                "/api/v1/auto-approval/rejection-breakdown?niche_id=gaming&window_days=30"
            )

        assert resp.status_code == 200
        body = resp.get_json()["data"]
        assert body["niche_id"] == "gaming"
        assert body["window_days"] == 30
        cats = body["categories"]
        assert len(cats) == 2
        # First entry — verify all 5 fields
        weak = cats[0]
        assert weak["feedback_category"] == "weak_hook"
        assert weak["count"] == 24
        assert weak["gate_agreed"] == 4
        assert weak["gate_disagreed"] == 20
        # disagreement_rate computed server-side: 20/24 ≈ 0.833
        assert weak["gate_disagreement_rate"] == pytest.approx(0.833, abs=1e-3)
        # Second entry — gate-mostly-agreed case (low disagreement)
        bf = cats[1]
        assert bf["gate_disagreement_rate"] == pytest.approx(0.2, abs=1e-3)

    def test_returns_empty_categories_on_cold_start(self, client):
        """No data in the window → empty categories list, 200 status
        (not 404 / error). Frontend renders 'no data yet'."""
        with patch(
            "genlab_core.scheduling.calibration_logger.breakdown_by_category",
            return_value=[],
        ):
            resp = client.get("/api/v1/auto-approval/rejection-breakdown?niche_id=gaming")

        assert resp.status_code == 200
        body = resp.get_json()["data"]
        assert body["categories"] == []

    def test_backend_exception_returns_500(self, client):
        """Calibration backend failure → 500 with error message,
        not a server crash that exposes internals."""
        with patch(
            "genlab_core.scheduling.calibration_logger.breakdown_by_category",
            side_effect=RuntimeError("simulated PG outage"),
        ):
            resp = client.get("/api/v1/auto-approval/rejection-breakdown?niche_id=gaming")

        assert resp.status_code == 500
        body = resp.get_json()
        assert "error" in body or "data" in body  # responses helper shape
