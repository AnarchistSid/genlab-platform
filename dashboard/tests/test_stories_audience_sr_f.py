"""PR #551 (2026-06-25): SR-F wire pass 12 — stories.py + audience.py
niche-scoped GET endpoints adopt the per-tenant filter.

Covers 4 distinct shapes:

  GET /api/v1/stories           — list (filter records by allowlist)
  GET /api/v1/stories/<id>      — per-record (403 on cross-tenant)
  GET /api/v1/audience/current  — dict[niche_id, ...] (filter outer keys)
  GET /api/v1/audience/history  — list of dicts with niche_id (filter + 403)

Pre-PR-551 a restricted operator could view another tenant's story
backlog (source URL + summary text) and audience growth (subscriber
counts + per-platform follower trends) — cross-tenant info disclosure
that would let a tenant-B operator monitor tenant-A's pipeline +
audience metrics.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

# ── source pins ────────────────────────────────────────────────────


def test_source_pin_stories_list_has_allowlist_filter():
    import server.api.stories as mod

    src = Path(mod.__file__).read_text()
    assert "get_allowed_niches" in src
    # The list_stories body has the filter line
    assert "in _allowed" in src
    assert "PR #551" in src


def test_source_pin_get_story_has_per_record_guard():
    import server.api.stories as mod

    src = Path(mod.__file__).read_text()
    # The per-record 403 path
    assert "Story belongs to niche" in src


def test_source_pin_audience_current_filters_outer_dict():
    import server.api.audience as mod

    src = Path(mod.__file__).read_text()
    assert "k: v for k, v in data.items() if k in _allowed" in src


def test_source_pin_audience_history_has_403_and_post_filter():
    import server.api.audience as mod

    src = Path(mod.__file__).read_text()
    # The 403 path for explicit cross-tenant niche_id query
    assert "Audience history scoped to niche" in src
    # The post-filter path for no-niche-id branch
    assert 'row.get("niche_id") in _allowed' in src


# ── behavioral pins ────────────────────────────────────────────────


def _scope_user_to_gaming(monkeypatch):
    monkeypatch.setenv("REVIEW_AUTH_USER", "gaming_op")
    monkeypatch.setenv("REVIEW_NICHE_ALLOWLIST_GAMING_OP", "gaming")
    monkeypatch.setattr(
        "server.auth.niche_allowlist._username_for_session",
        lambda: "gaming_op",
    )


def _make_stories_app():
    from flask import Flask
    from server.api.stories import bp

    app = Flask(__name__)
    app.register_blueprint(bp)
    return app


def _make_audience_app():
    from flask import Flask
    from server.api.audience import bp

    app = Flask(__name__)
    app.register_blueprint(bp)
    return app


def _story_records(*niches):
    return [
        {
            "id": f"st-{i}",
            "fields": {
                "niche_id": n,
                "title": f"story for {n}",
                "published_at": "2026-06-25T00:00:00Z",
            },
        }
        for i, n in enumerate(niches)
    ]


def test_stories_list_filters_cross_tenant(monkeypatch):
    """Gaming-scoped operator → only gaming stories in list view."""
    _scope_user_to_gaming(monkeypatch)

    fake_client = MagicMock()
    fake_client.stories.all.return_value = _story_records("gaming", "anime", "sports", "gaming")

    app = _make_stories_app()
    with patch("server.api.stories._get_client", return_value=fake_client):
        with app.test_client() as c:
            r = c.get("/api/v1/stories?niche_id=all")
    assert r.status_code == 200
    body = r.get_json()["data"]
    ids = {row["id"] for row in body["data"]}
    assert ids == {"st-0", "st-3"}


def test_get_story_403_on_cross_tenant(monkeypatch):
    """Per-story fetch outside allowlist → 403 + summary doesn't leak."""
    _scope_user_to_gaming(monkeypatch)

    fake_client = MagicMock()
    fake_client.stories.get.return_value = {
        "id": "st-1",
        "fields": {"niche_id": "anime", "title": "secret anime story"},
    }

    app = _make_stories_app()
    with patch("server.api.stories._get_client", return_value=fake_client):
        with app.test_client() as c:
            r = c.get("/api/v1/stories/st-1")
    assert r.status_code == 403
    assert "secret anime story" not in r.get_data(as_text=True)


def test_audience_current_filters_cross_tenant_keys(monkeypatch):
    """Gaming-scoped operator → only data['gaming'] returned."""
    _scope_user_to_gaming(monkeypatch)

    # Stub the DB layer so we don't need a real DATABASE_URL
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")

    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_conn.execute.return_value.fetchall.return_value = [
        {
            "niche_id": "gaming",
            "platform": "youtube",
            "metric_name": "subscribers",
            "metric_value": 1000,
            "snapshot_date": "2026-06-25",
        },
        {
            "niche_id": "anime",
            "platform": "youtube",
            "metric_name": "subscribers",
            "metric_value": 5000,
            "snapshot_date": "2026-06-25",
        },
    ]

    app = _make_audience_app()
    with patch("server.api.audience.pg_connect", return_value=fake_conn):
        with app.test_client() as c:
            r = c.get("/api/v1/audience/current")
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert "gaming" in body
    assert "anime" not in body  # filtered out by allowlist


def test_audience_history_403_on_cross_tenant_explicit_niche(monkeypatch):
    """Gaming-scoped operator passes niche_id=anime → 403."""
    _scope_user_to_gaming(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")

    app = _make_audience_app()
    with app.test_client() as c:
        r = c.get("/api/v1/audience/history?niche_id=anime")
    assert r.status_code == 403


def test_audience_history_filters_unrestricted_query(monkeypatch):
    """Gaming-scoped operator with no niche_id query → unfiltered
    DB query, but post-filter to gaming only."""
    _scope_user_to_gaming(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")

    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_conn.execute.return_value.fetchall.return_value = [
        {
            "niche_id": "gaming",
            "platform": "youtube",
            "metric_name": "subs",
            "metric_value": 100,
            "snapshot_date": "2026-06-25",
        },
        {
            "niche_id": "anime",
            "platform": "youtube",
            "metric_name": "subs",
            "metric_value": 500,
            "snapshot_date": "2026-06-25",
        },
    ]

    app = _make_audience_app()
    with patch("server.api.audience.pg_connect", return_value=fake_conn):
        with app.test_client() as c:
            r = c.get("/api/v1/audience/history")
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert all(row["niche_id"] == "gaming" for row in body)


def test_unrestricted_admin_sees_all_niches(monkeypatch):
    """Backward compat: admin → all niches' data returned."""
    monkeypatch.setenv("REVIEW_AUTH_USER", "admin")
    monkeypatch.delenv("REVIEW_NICHE_ALLOWLIST_ADMIN", raising=False)

    fake_client = MagicMock()
    fake_client.stories.all.return_value = _story_records("gaming", "anime", "sports", "movies")

    app = _make_stories_app()
    with patch("server.api.stories._get_client", return_value=fake_client):
        with app.test_client() as c:
            r = c.get("/api/v1/stories?niche_id=all")
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert len(body["data"]) == 4  # all 4 records visible
