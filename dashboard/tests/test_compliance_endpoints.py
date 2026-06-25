"""PR #578 (2026-06-25) — compliance + source-performance endpoints.

Pins:

  GET /api/v1/compliance/events
    * Default: 50 most-recent rows
    * Filters: niche_id, event_type, decision, limit
    * Validates enum filters server-side
    * SR-F: restricted operator sees only allowlist niches
    * 403 on explicit cross-tenant niche_id
    * UUID + datetime fields ISO-stringified for JSON

  GET /api/v1/compliance/checks
    * Returns currently-registered policy_gate check names
    * Reachable: false when compliance package unavailable

  GET /api/v1/learning/source-performance
    * niche_id required (400 when missing)
    * SR-F: 403 on explicit cross-tenant niche
    * Returns list ordered by reward_mean DESC (caller default)
    * Empty list when bandit has no data yet (cold-start)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

# ── Flask app fixtures ────────────────────────────────────────────


def _make_app_with_compliance_bp():
    from flask import Flask
    from server.api.compliance import bp

    app = Flask(__name__)
    app.register_blueprint(bp)
    return app


def _make_app_with_learning_bp():
    from flask import Flask
    from server.api.learning import bp

    app = Flask(__name__)
    app.register_blueprint(bp)
    return app


# ── GET /compliance/events ────────────────────────────────────────


def test_events_no_dsn_returns_503(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app = _make_app_with_compliance_bp()
    with app.test_client() as c:
        r = c.get("/api/v1/compliance/events")
    assert r.status_code == 503


def test_events_invalid_limit_returns_error(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    app = _make_app_with_compliance_bp()
    with app.test_client() as c:
        r = c.get("/api/v1/compliance/events?limit=not_a_number")
    assert r.status_code != 200


def test_events_invalid_event_type_returns_error(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    app = _make_app_with_compliance_bp()
    with app.test_client() as c:
        r = c.get("/api/v1/compliance/events?event_type=bogus_type")
    body = r.get_json() or {}
    assert "Invalid event_type" in (body.get("error", "") or str(body))


def test_events_invalid_decision_returns_error(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    app = _make_app_with_compliance_bp()
    with app.test_client() as c:
        r = c.get("/api/v1/compliance/events?decision=approve")
    body = r.get_json() or {}
    assert "Invalid decision" in (body.get("error", "") or str(body))


def test_events_happy_path_returns_normalised_rows(monkeypatch):
    """The endpoint normalises UUID + datetime to strings for JSON
    response. Pin the shape so frontend can rely on it."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")

    bp_id = uuid.uuid4()
    ev_id = uuid.uuid4()
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_cursor = MagicMock()
    fake_cursor.fetchall.return_value = [
        {
            "id": ev_id,
            "niche_id": "gaming",
            "blueprint_id": bp_id,
            "platform": "instagram",
            "event_type": "pre_publish_check",
            "decision": "warn",
            "reasons": ["spam_check:foo"],
            "metadata": {"k": "v"},
            "created_at": datetime(2026, 6, 25, 12, 0, 0, tzinfo=UTC),
        }
    ]
    fake_conn.execute.return_value = fake_cursor

    app = _make_app_with_compliance_bp()
    with patch("server.api.compliance.pg_connect", return_value=fake_conn):
        with app.test_client() as c:
            r = c.get("/api/v1/compliance/events")
    assert r.status_code == 200
    rows = r.get_json()["data"]
    assert len(rows) == 1
    assert rows[0]["id"] == str(ev_id)
    assert rows[0]["blueprint_id"] == str(bp_id)
    assert rows[0]["created_at"] == "2026-06-25T12:00:00+00:00"
    assert rows[0]["reasons"] == ["spam_check:foo"]
    assert rows[0]["metadata"] == {"k": "v"}


def test_events_403_on_cross_tenant_niche(monkeypatch):
    """SR-F: restricted operator can't query another niche's events."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    monkeypatch.setenv("REVIEW_AUTH_USER", "gaming_op")
    monkeypatch.setenv("REVIEW_NICHE_ALLOWLIST_GAMING_OP", "gaming")
    monkeypatch.setattr(
        "server.auth.niche_allowlist._username_for_session",
        lambda: "gaming_op",
    )

    app = _make_app_with_compliance_bp()
    with app.test_client() as c:
        r = c.get("/api/v1/compliance/events?niche_id=anime")
    assert r.status_code == 403


def test_events_post_filters_on_no_niche_query(monkeypatch):
    """When no niche_id query is passed by a scoped operator, the
    response is post-filtered to allowlist niches."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    monkeypatch.setenv("REVIEW_AUTH_USER", "gaming_op")
    monkeypatch.setenv("REVIEW_NICHE_ALLOWLIST_GAMING_OP", "gaming")
    monkeypatch.setattr(
        "server.auth.niche_allowlist._username_for_session",
        lambda: "gaming_op",
    )

    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_cursor = MagicMock()
    fake_cursor.fetchall.return_value = [
        {
            "id": uuid.uuid4(),
            "niche_id": "gaming",
            "blueprint_id": None,
            "platform": "instagram",
            "event_type": "pre_publish_check",
            "decision": "allow",
            "reasons": [],
            "metadata": {},
            "created_at": datetime(2026, 6, 25, 12, 0, 0, tzinfo=UTC),
        },
        {
            "id": uuid.uuid4(),
            "niche_id": "anime",  # cross-tenant — should be filtered out
            "blueprint_id": None,
            "platform": "instagram",
            "event_type": "pre_publish_check",
            "decision": "allow",
            "reasons": [],
            "metadata": {},
            "created_at": datetime(2026, 6, 25, 12, 0, 0, tzinfo=UTC),
        },
    ]
    fake_conn.execute.return_value = fake_cursor

    app = _make_app_with_compliance_bp()
    with patch("server.api.compliance.pg_connect", return_value=fake_conn):
        with app.test_client() as c:
            r = c.get("/api/v1/compliance/events")
    rows = r.get_json()["data"]
    assert len(rows) == 1
    assert rows[0]["niche_id"] == "gaming"


# ── GET /compliance/checks ────────────────────────────────────────


def test_checks_returns_registered_names():

    app = _make_app_with_compliance_bp()
    with app.test_client() as c:
        r = c.get("/api/v1/compliance/checks")
    body = r.get_json()["data"]
    assert body["reachable"] is True
    assert isinstance(body["checks"], list)
    # At minimum, the 3 Phase A checks (568+569+570) should be registered
    assert "copyright_attribution_check" in body["checks"]
    assert "spam_pattern_check" in body["checks"]
    assert "account_health_check" in body["checks"]
    assert body["count"] >= 3


def test_checks_handles_missing_compliance_gracefully():
    """If the compliance package isn't importable (degraded env),
    return reachable=false with reason instead of 500."""
    app = _make_app_with_compliance_bp()
    with patch.dict(
        "sys.modules",
        {"genlab_core.compliance.policy_gate": None},
    ):
        with app.test_client() as c:
            r = c.get("/api/v1/compliance/checks")
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert body["reachable"] is False
    assert body["count"] == 0
    assert "compliance package" in (body.get("reason") or "")


# ── GET /compliance/stats (PR after #581) ─────────────────────────


def test_stats_invalid_window_days_returns_400():
    """Garbage in window_days → 400 (defence before clamping)."""
    app = _make_app_with_compliance_bp()
    with app.test_client() as c:
        r = c.get("/api/v1/compliance/stats?window_days=not-a-number")
    body = r.get_json() or {}
    assert "window_days" in (body.get("error") or "")


def test_stats_uses_default_window_when_missing():
    """No window_days param → 7d default."""
    app = _make_app_with_compliance_bp()
    with patch("genlab_core.compliance.events.stats_by_niche", return_value={}) as mock_stats:
        with app.test_client() as c:
            r = c.get("/api/v1/compliance/stats")
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert body["window_days"] == 7
    mock_stats.assert_called_once_with(window_days=7)


def test_stats_clamps_oversized_window_at_endpoint():
    """window_days=9999 → clamped to 90 BEFORE the library call
    (defence in depth — library also clamps but we reject early)."""
    app = _make_app_with_compliance_bp()
    with patch("genlab_core.compliance.events.stats_by_niche", return_value={}) as mock_stats:
        with app.test_client() as c:
            r = c.get("/api/v1/compliance/stats?window_days=9999")
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert body["window_days"] == 90
    mock_stats.assert_called_once_with(window_days=90)


def test_stats_returns_per_niche_breakdown():
    """Library returns nested aggregation; endpoint passes through."""
    fake_stats = {
        "gaming": {
            "total": 12,
            "warns": 5,
            "blocks": 1,
            "allows": 6,
            "top_event_type": "spam_pattern_detected",
            "top_event_count": 4,
            "by_event_type": {
                "spam_pattern_detected": {"warn": 4, "block": 1, "allow": 0},
                "pre_publish_check": {"warn": 1, "block": 0, "allow": 6},
            },
        },
    }
    app = _make_app_with_compliance_bp()
    with patch("genlab_core.compliance.events.stats_by_niche", return_value=fake_stats):
        with app.test_client() as c:
            r = c.get("/api/v1/compliance/stats?window_days=7")
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert body["by_niche"]["gaming"]["warns"] == 5
    assert body["by_niche"]["gaming"]["top_event_type"] == "spam_pattern_detected"


def test_stats_sr_f_filters_to_allowlist():
    """Restricted operators see ONLY their niche's row in by_niche."""
    fake_stats = {
        "gaming": {
            "total": 1,
            "warns": 1,
            "blocks": 0,
            "allows": 0,
            "top_event_type": "x",
            "top_event_count": 1,
            "by_event_type": {},
        },
        "anime": {
            "total": 5,
            "warns": 5,
            "blocks": 0,
            "allows": 0,
            "top_event_type": "y",
            "top_event_count": 5,
            "by_event_type": {},
        },
    }
    app = _make_app_with_compliance_bp()
    with (
        patch("genlab_core.compliance.events.stats_by_niche", return_value=fake_stats),
        patch(
            "server.auth.niche_allowlist.get_allowed_niches",
            return_value={"gaming"},
        ),
    ):
        with app.test_client() as c:
            r = c.get("/api/v1/compliance/stats?window_days=7")
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert set(body["by_niche"].keys()) == {"gaming"}


def test_stats_handles_import_error_gracefully():
    """Pre-stats-PR core → empty by_niche, not 500."""
    app = _make_app_with_compliance_bp()
    with patch.dict("sys.modules", {"genlab_core.compliance.events": None}):
        with app.test_client() as c:
            r = c.get("/api/v1/compliance/stats?window_days=7")
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert body["by_niche"] == {}


# ── GET /learning/source-performance ──────────────────────────────


def test_source_perf_requires_niche_id(monkeypatch):
    app = _make_app_with_learning_bp()
    with app.test_client() as c:
        r = c.get("/api/v1/learning/source-performance")
    body = r.get_json() or {}
    assert "niche_id" in (body.get("error") or "")


def test_source_perf_403_on_cross_tenant_niche(monkeypatch):
    monkeypatch.setenv("REVIEW_AUTH_USER", "gaming_op")
    monkeypatch.setenv("REVIEW_NICHE_ALLOWLIST_GAMING_OP", "gaming")
    monkeypatch.setattr(
        "server.auth.niche_allowlist._username_for_session",
        lambda: "gaming_op",
    )

    app = _make_app_with_learning_bp()
    with app.test_client() as c:
        r = c.get("/api/v1/learning/source-performance?niche_id=anime")
    assert r.status_code == 403


def test_source_perf_empty_on_cold_start(monkeypatch):
    """When the bandit hasn't accumulated data yet (cold start), the
    endpoint returns empty list — not 500. Aligns with PR #571's
    fail-OPEN list_source_performance contract."""
    from genlab_core.learning import source_performance as sp_mod

    app = _make_app_with_learning_bp()
    with patch.object(sp_mod, "list_source_performance", return_value=[]):
        with app.test_client() as c:
            r = c.get("/api/v1/learning/source-performance?niche_id=gaming")
    assert r.status_code == 200
    assert r.get_json()["data"] == []


def test_source_perf_returns_dto_shape(monkeypatch):
    """Pin the response shape: niche_id, source, alpha, beta, n_plays,
    reward_mean. Frontend dashboard card consumes these exact fields."""
    from genlab_core.learning import source_performance as sp_mod
    from genlab_core.learning.source_performance import SourcePerformance

    fake = [
        SourcePerformance(
            niche_id="gaming",
            source="youtube_trending",
            alpha=8.0,
            beta=2.0,
            n_plays=10,
            reward_mean=0.8,
        ),
        SourcePerformance(
            niche_id="gaming",
            source="youtube_search",
            alpha=3.0,
            beta=7.0,
            n_plays=10,
            reward_mean=0.3,
        ),
    ]
    app = _make_app_with_learning_bp()
    with patch.object(sp_mod, "list_source_performance", return_value=fake):
        with app.test_client() as c:
            r = c.get("/api/v1/learning/source-performance?niche_id=gaming")
    rows = r.get_json()["data"]
    assert len(rows) == 2
    assert rows[0]["source"] == "youtube_trending"
    assert rows[0]["reward_mean"] == 0.8
    assert rows[0]["alpha"] == 8.0
    assert rows[0]["beta"] == 2.0
    assert rows[0]["n_plays"] == 10


def test_source_perf_handles_import_error_gracefully():
    """Pre-PR-571 core → ImportError → empty list, not 500."""
    app = _make_app_with_learning_bp()
    with patch.dict(
        "sys.modules",
        {"genlab_core.learning.source_performance": None},
    ):
        with app.test_client() as c:
            r = c.get("/api/v1/learning/source-performance?niche_id=gaming")
    assert r.status_code == 200
    assert r.get_json()["data"] == []
