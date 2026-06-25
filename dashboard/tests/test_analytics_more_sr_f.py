"""PR #556 (2026-06-25): SR-F wire pass 15 — 4 more analytics routes.

Covers the remaining niche-scoped routes in analytics.py:

  GET /api/v1/analytics/content       — query?niche_id=X (403 + "all" trim)
  GET /api/v1/analytics/funnel        — query?niche_id=X (403 + "all" trim)
  GET /api/v1/analytics/monetization  — global aggregate (SQL scoped)
  GET /api/v1/analytics/cross-niche   — per-niche dict (outer key trim)

Three different SR-F shapes in one PR:
  1. Hybrid 403/filter — the operator can pass niche_id=X explicitly OR
     niche_id=all; explicit cross-tenant → 403, "all" → post-filter the
     blueprint list to allowlist niches before downstream aggregation.
  2. SQL-scoped — no niche_id query param; we parameterise each SQL
     query with WHERE niche_id = ANY(%s) when the operator is scoped.
  3. Per-niche-dict outer-key trim — same shape as /audience/current
     from PR #551.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

# ── source pins ────────────────────────────────────────────────────


def test_source_pin_content_funnel_have_403_guard():
    import server.api.analytics as mod

    src = Path(mod.__file__).read_text()
    assert "Content analytics scoped to niche" in src
    assert "Funnel analytics scoped to niche" in src
    assert "PR #556" in src


def test_source_pin_monetization_has_sql_scoping():
    """The global aggregate queries must add WHERE niche_id = ANY(%s)
    when the operator is scoped — otherwise total_published etc would
    still leak global counts to restricted operators."""
    import server.api.analytics as mod

    src = Path(mod.__file__).read_text()
    assert "AND niche_id = ANY(%s)" in src


def test_source_pin_cross_niche_dict_trimmed():
    import server.api.analytics as mod

    src = Path(mod.__file__).read_text()
    assert "result = {k: v for k, v in result.items() if k in _allowed}" in src


# ── behavioral pins ────────────────────────────────────────────────


def _scope_user_to_gaming(monkeypatch):
    monkeypatch.setenv("REVIEW_AUTH_USER", "gaming_op")
    monkeypatch.setenv("REVIEW_NICHE_ALLOWLIST_GAMING_OP", "gaming")
    monkeypatch.setattr(
        "server.auth.niche_allowlist._username_for_session",
        lambda: "gaming_op",
    )


def _make_app():
    from flask import Flask
    from server.api.analytics import bp

    app = Flask(__name__)
    app.register_blueprint(bp)
    return app


def _bp_records(*niches):
    return [
        {
            "id": f"bp-{i}",
            "fields": {
                "niche_id": n,
                "status": "PUBLISHED",
                "template_id": f"tmpl-{n}",
                "generation_cost_usd": 0.05,
            },
        }
        for i, n in enumerate(niches)
    ]


# /content


def test_content_403_on_cross_tenant_explicit_niche(monkeypatch):
    _scope_user_to_gaming(monkeypatch)
    app = _make_app()
    with app.test_client() as c:
        r = c.get("/api/v1/analytics/content?niche_id=anime")
    assert r.status_code == 403
    assert "anime" in r.get_data(as_text=True)


def test_content_all_mode_trims_to_allowlist(monkeypatch):
    """niche_id=all should NOT show cross-tenant template counts to a
    restricted operator. Pin: template aggregation runs ONLY over the
    allowlist niches."""
    _scope_user_to_gaming(monkeypatch)

    fake_client = MagicMock()
    fake_client.blueprints.all.return_value = _bp_records("gaming", "anime", "gaming", "sports")

    app = _make_app()
    with patch("server.api.analytics._get_client", return_value=fake_client):
        with app.test_client() as c:
            r = c.get("/api/v1/analytics/content?niche_id=all")
    assert r.status_code == 200
    body = r.get_json()["data"]
    # 2 gaming blueprints → 1 template (both share template_id=tmpl-gaming)
    # with total=2 published=2. Cross-tenant templates excluded.
    assert len(body["data"]) == 1
    assert body["data"][0]["template_id"] == "tmpl-gaming"
    assert body["data"][0]["total"] == 2


# /funnel


def test_funnel_403_on_cross_tenant_explicit_niche(monkeypatch):
    _scope_user_to_gaming(monkeypatch)
    app = _make_app()
    with app.test_client() as c:
        r = c.get("/api/v1/analytics/funnel?niche_id=movies")
    assert r.status_code == 403


def test_funnel_all_mode_trims_costs(monkeypatch):
    """total_cost in funnel must reflect only the operator's scope —
    a restricted operator must not see cross-tenant LLM spend."""
    _scope_user_to_gaming(monkeypatch)

    fake_client = MagicMock()
    # 2 gaming + 2 anime → only gaming costs should sum
    fake_client.blueprints.all.return_value = _bp_records("gaming", "anime", "gaming", "anime")

    app = _make_app()
    with patch("server.api.analytics._get_client", return_value=fake_client):
        with app.test_client() as c:
            r = c.get("/api/v1/analytics/funnel?niche_id=all")
    body = r.get_json()["data"]
    # 2 gaming blueprints, each with 0.05 cost → 0.10
    assert body["total_cost"] == 0.10
    # PUBLISHED stage count == 2 (gaming only)
    pub = next(s for s in body["data"] if s["stage"] == "PUBLISHED")
    assert pub["count"] == 2


# /monetization


def test_monetization_unscoped_admin_runs_global_query(monkeypatch):
    """Backward compat: admin path runs the existing global-aggregate
    queries (no niche_id binding). Pin via the SQL the cursor sees."""
    monkeypatch.setenv("REVIEW_AUTH_USER", "admin")
    monkeypatch.delenv("REVIEW_NICHE_ALLOWLIST_ADMIN", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")

    fake_cursor = MagicMock()
    # fetchone for COUNT(*) returns mapping with cnt; fetchall returns []
    fake_cursor.fetchone.return_value = {"cnt": 7}
    fake_cursor.fetchall.return_value = []
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_conn.execute.return_value = fake_cursor

    app = _make_app()
    with patch("server.api.analytics.pg_connect", return_value=fake_conn):
        with app.test_client() as c:
            r = c.get("/api/v1/analytics/monetization")
    assert r.status_code == 200
    # Capture the SQL: 3 execute() calls, none of them should bind niche_id
    sql_calls = [str(call.args[0]) for call in fake_conn.execute.call_args_list]
    assert all("ANY(%s)" not in s for s in sql_calls), (
        f"admin path should not parameterise on niche; got: {sql_calls}"
    )


def test_monetization_scoped_uses_any_binding(monkeypatch):
    """Restricted operator → every query binds niche_id via ANY(%s).
    Pin both the SQL shape and that the bound list is the allowlist."""
    _scope_user_to_gaming(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")

    fake_cursor = MagicMock()
    fake_cursor.fetchone.return_value = {"cnt": 3}
    fake_cursor.fetchall.return_value = []
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_conn.execute.return_value = fake_cursor

    app = _make_app()
    with patch("server.api.analytics.pg_connect", return_value=fake_conn):
        with app.test_client() as c:
            r = c.get("/api/v1/analytics/monetization")
    assert r.status_code == 200
    # All 3 queries bind niche_id; the bound parameter is (["gaming"],)
    bind_calls = [
        call for call in fake_conn.execute.call_args_list if "ANY(%s)" in str(call.args[0])
    ]
    assert len(bind_calls) == 3
    for call in bind_calls:
        # call.args = (sql, (params,))
        assert call.args[1] == (["gaming"],)


# /cross-niche


def test_cross_niche_restricted_trims_outer_keys(monkeypatch):
    """Per-niche dict shape: outer keys are niche_ids. Restricted
    operator sees only their niches' rows."""
    _scope_user_to_gaming(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")

    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn

    # First query: engagement aggregates (returns 3 niches)
    # Second + third: publishing + clicks (return empty for simplicity)
    fake_conn.execute.side_effect = lambda *a, **kw: MagicMock(
        fetchall=lambda: (
            [
                {
                    "niche_id": "gaming",
                    "records": 5,
                    "total_reach": 1000,
                    "total_likes": 50,
                    "total_comments": 10,
                },
                {
                    "niche_id": "anime",
                    "records": 10,
                    "total_reach": 5000,
                    "total_likes": 200,
                    "total_comments": 40,
                },
                {
                    "niche_id": "sports",
                    "records": 3,
                    "total_reach": 500,
                    "total_likes": 20,
                    "total_comments": 5,
                },
            ]
            if "FROM analytics" in str(a[0])
            else []
        )
    )

    app = _make_app()
    with patch("server.api.analytics.pg_connect", return_value=fake_conn):
        with app.test_client() as c:
            r = c.get("/api/v1/analytics/cross-niche")
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert set(body.keys()) == {"gaming"}
    assert body["gaming"]["total_reach"] == 1000


def test_cross_niche_unrestricted_returns_all_niches(monkeypatch):
    """Admin sees the full per-niche comparison shape."""
    monkeypatch.setenv("REVIEW_AUTH_USER", "admin")
    monkeypatch.delenv("REVIEW_NICHE_ALLOWLIST_ADMIN", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")

    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_conn.execute.side_effect = lambda *a, **kw: MagicMock(
        fetchall=lambda: (
            [
                {
                    "niche_id": "gaming",
                    "records": 5,
                    "total_reach": 1000,
                    "total_likes": 50,
                    "total_comments": 10,
                },
                {
                    "niche_id": "anime",
                    "records": 10,
                    "total_reach": 5000,
                    "total_likes": 200,
                    "total_comments": 40,
                },
            ]
            if "FROM analytics" in str(a[0])
            else []
        )
    )

    app = _make_app()
    with patch("server.api.analytics.pg_connect", return_value=fake_conn):
        with app.test_client() as c:
            r = c.get("/api/v1/analytics/cross-niche")
    body = r.get_json()["data"]
    assert set(body.keys()) == {"gaming", "anime"}
