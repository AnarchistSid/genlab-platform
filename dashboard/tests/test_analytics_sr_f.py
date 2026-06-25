"""PR #552 (2026-06-25): SR-F wire pass 13 — analytics endpoints.

Covers two shapes:
  GET /api/v1/analytics/top-posts — flat list, filter BEFORE top-20
  GET /api/v1/analytics/overview  — niche-keyed aggregator, 403 on
                                    cross-tenant explicit niche_id

`/top-posts` filtering happens BEFORE the sort+top-20 cut so a
restricted operator sees the top 20 within THEIR niches (not the
global top 20 minus cross-tenant rows, which would yield <20).

The unrestricted aggregate mode of `/overview` (niche_id=all) is
intentionally left untrimmed for this PR — drilling into the
response shape of _build_overview to per-niche-filter the nested
breakdowns is a follow-up. The 403 guard on explicit niche_id
covers the leak-through path.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch


def test_source_pin_top_posts_filters_before_top_20():
    """The allowlist filter MUST run before posts.sort+top-20 cut,
    so restricted operators get their niche's top 20 (not a partial
    global top 20). Pin the order."""
    import server.api.analytics as mod

    src = Path(mod.__file__).read_text()
    filter_idx = src.find('p for p in posts if (p.get("niche_id") or "") in _allowed')
    sort_idx = src.find('posts.sort(key=lambda p: p["likes"], reverse=True)')
    top_idx = src.find("top = posts[:20]")
    assert filter_idx != -1 and sort_idx != -1 and top_idx != -1
    assert filter_idx < sort_idx < top_idx, (
        "SR-F filter must run BEFORE sort + top-20 cut. Otherwise "
        "restricted operators see a partial global top 20."
    )
    assert "PR #552" in src


def test_source_pin_overview_403_on_cross_tenant_niche():
    import server.api.analytics as mod

    src = Path(mod.__file__).read_text()
    assert "Analytics overview scoped to niche" in src


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


def _records(*niche_likes_pairs):
    """Build analytics records — pairs of (niche, likes)."""
    return [
        {
            "id": f"p-{i}",
            "fields": {
                "post_id": f"post-{i}",
                "platform": "instagram",
                "niche_id": niche,
                "likes": likes,
                "comments": 0,
                "reach": 100,
            },
        }
        for i, (niche, likes) in enumerate(niche_likes_pairs)
    ]


def test_top_posts_filters_cross_tenant_before_top_20(monkeypatch):
    """Gaming-scoped operator: top-posts returns ONLY gaming posts,
    not 'global top 20 minus cross-tenant' which would shrink the
    list below 20. The pin verifies the filter shape."""
    _scope_user_to_gaming(monkeypatch)

    # 5 gaming + 5 anime posts. Gaming-scoped op should get 5 gaming
    # records (top 20 of their visible set, which is < 20).
    pairs = (
        [("gaming", 100), ("anime", 200), ("gaming", 90), ("anime", 180)]
        + [("gaming", 80), ("anime", 170), ("gaming", 70)]
        + [("anime", 160), ("gaming", 60), ("sports", 999)]
    )
    fake_records = _records(*pairs)

    app = _make_app()
    with patch("server.api.analytics._cached_analytics_all", return_value=fake_records):
        with app.test_client() as c:
            r = c.get("/api/v1/analytics/top-posts")
    assert r.status_code == 200
    body = r.get_json()["data"]
    # Only gaming niche records survived the filter
    niches_in_response = {p["niche_id"] for p in body["posts"]}
    assert niches_in_response == {"gaming"}
    assert len(body["posts"]) == 5  # all 5 gaming records


def test_top_posts_unrestricted_returns_global_top_20(monkeypatch):
    """Backward compat: admin sees the global top 20 unfiltered."""
    monkeypatch.setenv("REVIEW_AUTH_USER", "admin")
    monkeypatch.delenv("REVIEW_NICHE_ALLOWLIST_ADMIN", raising=False)

    pairs = [("gaming", 100), ("anime", 200), ("sports", 999)]
    fake_records = _records(*pairs)

    app = _make_app()
    with patch("server.api.analytics._cached_analytics_all", return_value=fake_records):
        with app.test_client() as c:
            r = c.get("/api/v1/analytics/top-posts")
    assert r.status_code == 200
    body = r.get_json()["data"]
    # All 3 records present, sorted by likes desc
    assert [p["niche_id"] for p in body["posts"]] == ["sports", "anime", "gaming"]


def test_overview_403_on_explicit_cross_tenant_niche(monkeypatch):
    """Gaming-scoped operator queries /overview?niche_id=anime → 403."""
    _scope_user_to_gaming(monkeypatch)

    app = _make_app()
    with app.test_client() as c:
        r = c.get("/api/v1/analytics/overview?niche_id=anime")
    assert r.status_code == 403
    assert "anime" in r.get_data(as_text=True)


def test_overview_all_mode_passes_guard_for_restricted_user(monkeypatch):
    """niche_id=all is the default; restricted operator still gets
    through the 403 path (the aggregate may still leak info; that's
    a deferred follow-up). Pin that the guard doesn't 403 on 'all'."""
    _scope_user_to_gaming(monkeypatch)

    app = _make_app()
    with (
        patch("server.api.analytics._build_overview", return_value={"stub": True}),
    ):
        with app.test_client() as c:
            r = c.get("/api/v1/analytics/overview?niche_id=all")
    assert r.status_code != 403


def test_overview_same_tenant_proceeds(monkeypatch):
    """Gaming operator queries gaming overview → guard passes."""
    _scope_user_to_gaming(monkeypatch)

    app = _make_app()
    with patch("server.api.analytics._build_overview", return_value={"stub": True}):
        with app.test_client() as c:
            r = c.get("/api/v1/analytics/overview?niche_id=gaming")
    assert r.status_code != 403
