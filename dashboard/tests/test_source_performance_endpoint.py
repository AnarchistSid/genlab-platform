"""Source-performance dashboard endpoint — Phase 2 L1 (2026-06-30).

Pins for ``GET /api/v1/source-performance``:

  * niche_id required (400 when missing)
  * Unknown niche_id → 400
  * 403 when niche_id outside operator SR-F allowlist
  * Garbage int params → 400
  * Defensive clamps applied at endpoint level (top_n)
  * Empty list returned cleanly when library returns []
  * SourcePerformance dataclasses serialise to JSON-correct shape
  * Beta CI bounds present + clamped to [0, 1]
  * ImportError on library → graceful empty response with warning
  * Library exception → 500
"""

from __future__ import annotations

from unittest.mock import patch


def _make_app():
    from flask import Flask
    from server.api.source_performance import bp

    app = Flask(__name__)
    app.register_blueprint(bp)
    return app


# ── input validation ─────────────────────────────────────────────


def test_source_performance_requires_niche_id():
    app = _make_app()
    with app.test_client() as c:
        r = c.get("/api/v1/source-performance")
    assert r.status_code == 400
    assert "niche_id" in (r.get_json() or {}).get("error", "")


def test_source_performance_unknown_niche_id_returns_400():
    app = _make_app()
    with app.test_client() as c:
        r = c.get("/api/v1/source-performance?niche_id=not_a_niche")
    assert r.status_code == 400
    assert "Unknown niche" in (r.get_json() or {}).get("error", "")


def test_source_performance_garbage_top_n_returns_400():
    app = _make_app()
    with app.test_client() as c:
        r = c.get("/api/v1/source-performance?niche_id=ai_creators&top_n=xyz")
    assert r.status_code == 400
    assert "top_n" in (r.get_json() or {}).get("error", "")


def test_source_performance_clamps_oversized_top_n():
    app = _make_app()
    with patch(
        "genlab_core.learning.source_performance.list_source_performance",
        return_value=[],
    ):
        with app.test_client() as c:
            r = c.get("/api/v1/source-performance?niche_id=gaming&top_n=99999")
    assert r.status_code == 200
    body = r.get_json()["data"]
    # Clamp upper bound is 100
    assert body["top_n"] == 100


# ── SR-F enforcement ─────────────────────────────────────────────


def test_source_performance_403_on_cross_tenant_niche():
    app = _make_app()
    with patch(
        "server.auth.niche_allowlist.get_allowed_niches",
        return_value={"gaming"},
    ):
        with app.test_client() as c:
            r = c.get("/api/v1/source-performance?niche_id=anime")
    assert r.status_code == 403


# ── happy path ───────────────────────────────────────────────────


def test_source_performance_empty_list_when_no_data():
    app = _make_app()
    with patch(
        "genlab_core.learning.source_performance.list_source_performance",
        return_value=[],
    ):
        with app.test_client() as c:
            r = c.get("/api/v1/source-performance?niche_id=gaming")
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert body["niche_id"] == "gaming"
    assert body["sources"] == []
    # Default top_n
    assert body["top_n"] == 20


def test_source_performance_serialises_dataclass_correctly():
    from genlab_core.learning.source_performance import SourcePerformance

    fake = [
        SourcePerformance(
            niche_id="ai_creators",
            source="reddit:aivideo",
            alpha=12.3,
            beta=31.7,
            n_plays=42,
            reward_mean=12.3 / (12.3 + 31.7),
        ),
    ]
    app = _make_app()
    with patch(
        "genlab_core.learning.source_performance.list_source_performance",
        return_value=fake,
    ):
        with app.test_client() as c:
            r = c.get("/api/v1/source-performance?niche_id=ai_creators")
    assert r.status_code == 200
    sources = r.get_json()["data"]["sources"]
    assert len(sources) == 1
    s = sources[0]
    assert s["arm_id"] == "source:reddit:aivideo"
    assert s["source"] == "reddit:aivideo"
    assert s["niche_id"] == "ai_creators"
    assert s["n_plays"] == 42
    assert s["alpha"] == 12.3
    assert s["beta"] == 31.7
    # CI bounds present and clamped to [0, 1]
    assert 0.0 <= s["reward_ci_lower"] <= s["reward_mean"]
    assert s["reward_mean"] <= s["reward_ci_upper"] <= 1.0


def test_source_performance_respects_top_n_truncation():
    """top_n=3 returns at most 3 rows, even if library returns more."""
    from genlab_core.learning.source_performance import SourcePerformance

    fake = [
        SourcePerformance(
            niche_id="gaming",
            source=f"source_{i}",
            alpha=float(i + 1),
            beta=1.0,
            n_plays=i + 1,
            reward_mean=(i + 1) / (i + 2),
        )
        for i in range(10)
    ]
    app = _make_app()
    with patch(
        "genlab_core.learning.source_performance.list_source_performance",
        return_value=fake,
    ):
        with app.test_client() as c:
            r = c.get("/api/v1/source-performance?niche_id=gaming&top_n=3")
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert len(body["sources"]) == 3


# ── graceful degradation ─────────────────────────────────────────


def test_source_performance_handles_import_error_gracefully():
    """Pre-PR-571 core (no source_performance module) → empty payload
    with warning, not 500."""
    app = _make_app()
    with patch.dict(
        "sys.modules",
        {"genlab_core.learning.source_performance": None},
    ):
        with app.test_client() as c:
            r = c.get("/api/v1/source-performance?niche_id=ai_creators")
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert body["sources"] == []
    assert "source_performance module unavailable" in body.get("warning", "")


def test_source_performance_returns_500_when_library_raises():
    """Unexpected library exception (not the fail-OPEN path) → 500."""
    app = _make_app()
    with patch(
        "genlab_core.learning.source_performance.list_source_performance",
        side_effect=RuntimeError("unexpected DB error"),
    ):
        with app.test_client() as c:
            r = c.get("/api/v1/source-performance?niche_id=ai_creators")
    assert r.status_code == 500
