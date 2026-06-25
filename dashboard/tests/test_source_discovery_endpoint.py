"""Source-discovery dashboard endpoint — PR after #586.

Pins for ``GET /api/v1/source-discovery/proposals``:

  * niche_id required (400 when missing)
  * 403 when niche_id outside operator SR-F allowlist
  * Garbage int params → 400 (not 500)
  * Defensive clamps applied at endpoint level (window_days/min_clips/top_n)
  * Empty proposals returned cleanly when library returns []
  * Suggestion dataclasses serialise to JSON-correct shape
  * ImportError on library → graceful empty response with warning
  * Library exception → 500
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch


def _make_app():
    from flask import Flask
    from server.api.source_discovery import bp

    app = Flask(__name__)
    app.register_blueprint(bp)
    return app


# ── input validation ─────────────────────────────────────────────


def test_proposals_requires_niche_id():
    app = _make_app()
    with app.test_client() as c:
        r = c.get("/api/v1/source-discovery/proposals")
    assert r.status_code != 200
    assert "niche_id" in (r.get_json() or {}).get("error", "")


def test_proposals_garbage_ints_return_400():
    app = _make_app()
    with app.test_client() as c:
        r = c.get("/api/v1/source-discovery/proposals?niche_id=gaming&window_days=abc")
    body = r.get_json() or {}
    assert "must be integers" in (body.get("error") or "")


def test_proposals_endpoint_clamps_oversized_params():
    """window_days=9999 clamped to 180 BEFORE library call."""
    app = _make_app()
    with patch(
        "genlab_core.source_discovery.propose_source_channels",
        return_value=[],
    ) as mock_lib:
        with app.test_client() as c:
            r = c.get(
                "/api/v1/source-discovery/proposals"
                "?niche_id=gaming&window_days=9999&min_clips=0&top_n=5000"
            )
    assert r.status_code == 200
    # All three should be clamped at the endpoint
    kwargs = mock_lib.call_args.kwargs
    assert kwargs["window_days"] == 180
    assert kwargs["min_clips"] == 1
    assert kwargs["top_n"] == 100


# ── SR-F enforcement ─────────────────────────────────────────────


def test_proposals_403_on_cross_tenant_niche():
    app = _make_app()
    with patch(
        "server.auth.niche_allowlist.get_allowed_niches",
        return_value={"gaming"},
    ):
        with app.test_client() as c:
            r = c.get("/api/v1/source-discovery/proposals?niche_id=anime")
    assert r.status_code == 403


# ── happy path ───────────────────────────────────────────────────


def test_proposals_empty_when_library_returns_empty():
    """Most common case post-PR-586 deploy: no source channels
    accumulated yet → empty proposals → card renders zero-state."""
    app = _make_app()
    with patch(
        "genlab_core.source_discovery.propose_source_channels",
        return_value=[],
    ):
        with app.test_client() as c:
            r = c.get("/api/v1/source-discovery/proposals?niche_id=gaming")
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert body["niche_id"] == "gaming"
    assert body["window_days"] == 30
    assert body["proposals"] == []


def test_proposals_serialise_channel_suggestion_correctly():
    from genlab_core.source_discovery import ChannelSuggestion

    fake_suggestions = [
        ChannelSuggestion(
            source_channel_id="UCabc",
            clips_used=5,
            total_engagement=25000.0,
            avg_engagement=5000.0,
            last_used_at=datetime(2026, 6, 20, 12, 0, tzinfo=UTC),
            sample_blueprints=["cand1", "cand2"],
        ),
    ]
    app = _make_app()
    with patch(
        "genlab_core.source_discovery.propose_source_channels",
        return_value=fake_suggestions,
    ):
        with app.test_client() as c:
            r = c.get("/api/v1/source-discovery/proposals?niche_id=gaming")
    body = r.get_json()["data"]
    assert len(body["proposals"]) == 1
    p = body["proposals"][0]
    assert p["source_channel_id"] == "UCabc"
    assert p["clips_used"] == 5
    assert p["avg_engagement"] == 5000.0
    # datetime → ISO string
    assert p["last_used_at"].startswith("2026-06-20T12:00:00")
    assert p["sample_blueprints"] == ["cand1", "cand2"]


def test_proposals_passes_through_window_days_default():
    """No window_days param → 30 default propagates to library."""
    app = _make_app()
    with patch(
        "genlab_core.source_discovery.propose_source_channels",
        return_value=[],
    ) as mock_lib:
        with app.test_client() as c:
            r = c.get("/api/v1/source-discovery/proposals?niche_id=gaming")
    assert r.status_code == 200
    assert mock_lib.call_args.kwargs["window_days"] == 30


# ── graceful degradation ─────────────────────────────────────────


def test_proposals_handles_import_error_gracefully():
    """Pre-PR-586 core (no source_discovery module) → empty payload
    with warning, not 500."""
    app = _make_app()
    with patch.dict("sys.modules", {"genlab_core.source_discovery": None}):
        with app.test_client() as c:
            r = c.get("/api/v1/source-discovery/proposals?niche_id=gaming")
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert body["proposals"] == []
    assert "source_discovery module unavailable" in body.get("warning", "")


def test_proposals_returns_500_when_library_raises():
    """Unexpected library exception (not the fail-OPEN path) → 500."""
    app = _make_app()
    with patch(
        "genlab_core.source_discovery.propose_source_channels",
        side_effect=RuntimeError("unexpected DB error"),
    ):
        with app.test_client() as c:
            r = c.get("/api/v1/source-discovery/proposals?niche_id=gaming")
    assert r.status_code == 500
