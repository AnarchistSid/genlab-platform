"""PR #546 (2026-06-25): SR-F wire pass 7 — config_routes.py niche-keyed
endpoints adopt the per-tenant guard.

Companion to PRs #539-#545. Config endpoints take ``niche_id`` as a
query-string param and write to the corresponding niche's YAML files
(sources, publishing rollout config). A tenant-B operator with no
allowlist constraint could previously add/delete sources or flip
auto-publish rollout for tenant-A's niche by changing one URL param.

This PR closes that surface for **all 11 niche-keyed endpoints**:

  PATCH /api/v1/config/auto-publish              (1)
  POST  /api/v1/config/sources/youtube-channels  (1)
  DELETE /api/v1/config/sources/youtube-channels (1)
  POST  /api/v1/config/sources/rss-feeds         (1)
  DELETE /api/v1/config/sources/rss-feeds        (1)
  POST  /api/v1/config/sources/subreddits        (1)
  DELETE /api/v1/config/sources/subreddits       (1)
  Plus 4 niche-keyed GET handlers that also accept niche_id
  (sources/* list endpoints) — pinning ≥11 catches drops on either
  side of the mutation/read line.

If these pins regress, a tenant-B operator could mutate tenant-A's
config files via known URLs (auto_publish.rollout_pct → 1.0 to
auto-approve everything; add a malicious source URL; delete the
legitimate sources).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

# ── source pins ─────────────────────────────────────────────────────


def test_niche_keyed_guard_helper_present():
    """The helper that takes niche_id directly (not via blueprint fetch)
    must exist + reuse the shared get_allowed_niches resolver."""
    import server.api.config_routes as mod

    src = Path(mod.__file__).read_text()
    assert "def _enforce_niche_keyed_allowlist(niche_id: str)" in src
    # Uses the foundation resolver from PR #539
    assert "from server.auth.niche_allowlist import get_allowed_niches" in src


def test_all_11_niche_keyed_endpoints_call_guard():
    """All endpoints that accept niche_id as a query-string param
    must call the guard. Pin at ≥11 occurrences."""
    import server.api.config_routes as mod

    src = Path(mod.__file__).read_text()
    count = src.count("_enforce_niche_keyed_allowlist(niche_id)")
    assert count >= 11, (
        f"Expected ≥11 _enforce_niche_keyed_allowlist calls; found {count}. "
        f"Each lost guard = a config-mutation surface that ignores SR-F."
    )


def test_pr_546_anchor_present():
    """Future archaeologist anchor."""
    import server.api.config_routes as mod

    src = Path(mod.__file__).read_text()
    assert "PR #546" in src


# ── behavioral pins ────────────────────────────────────────────────


def _make_app():
    from flask import Flask
    from server.api.config_routes import bp, settings_bp

    app = Flask(__name__)
    app.register_blueprint(bp)
    app.register_blueprint(settings_bp)
    return app


def _scope_user_to_gaming(monkeypatch):
    """dotenv-override workaround from earlier SR-F tests."""
    monkeypatch.setenv("REVIEW_AUTH_USER", "gaming_op")
    monkeypatch.setenv("REVIEW_NICHE_ALLOWLIST_GAMING_OP", "gaming")
    monkeypatch.setattr(
        "server.auth.niche_allowlist._username_for_session",
        lambda: "gaming_op",
    )


def test_auto_publish_patch_403_on_cross_tenant(monkeypatch):
    """The highest-impact niche-keyed config endpoint — flipping
    rollout_pct could auto-approve every blueprint in a niche."""
    _scope_user_to_gaming(monkeypatch)

    app = _make_app()
    with app.test_client() as c:
        r = c.patch(
            "/api/v1/config/auto-publish?niche_id=anime",
            json={"rollout_pct": 1.0},
        )
    assert r.status_code == 403
    body = r.get_data(as_text=True)
    assert "anime" in body
    assert "SR-F" in body or "PR #546" in body


def test_sources_youtube_post_403_on_cross_tenant(monkeypatch):
    """Adding a YouTube channel to another tenant's sources — blocked."""
    _scope_user_to_gaming(monkeypatch)

    app = _make_app()
    with app.test_client() as c:
        r = c.post(
            "/api/v1/config/sources/youtube-channels?niche_id=movies",
            json={"url": "https://youtube.com/@evil", "name": "evil"},
        )
    assert r.status_code == 403


def test_sources_youtube_delete_403_on_cross_tenant(monkeypatch):
    """DELETE on another tenant's source — blocked (would silently
    sabotage that tenant's content pipeline)."""
    _scope_user_to_gaming(monkeypatch)

    app = _make_app()
    with app.test_client() as c:
        r = c.delete(
            "/api/v1/config/sources/youtube-channels?niche_id=sports&url=https%3A%2F%2Fyoutube.com%2F%40legit"
        )
    assert r.status_code == 403


def test_sources_rss_post_403_on_cross_tenant(monkeypatch):
    _scope_user_to_gaming(monkeypatch)
    app = _make_app()
    with app.test_client() as c:
        r = c.post(
            "/api/v1/config/sources/rss-feeds?niche_id=anime",
            json={"url": "https://example.com/feed", "name": "x"},
        )
    assert r.status_code == 403


def test_sources_subreddits_post_403_on_cross_tenant(monkeypatch):
    _scope_user_to_gaming(monkeypatch)
    app = _make_app()
    with app.test_client() as c:
        r = c.post(
            "/api/v1/config/sources/subreddits?niche_id=anime",
            json={"name": "r/EvilAnime"},
        )
    assert r.status_code == 403


def test_same_tenant_request_proceeds(monkeypatch):
    """Gaming-scoped operator on gaming config → guard passes, the
    endpoint runs its normal flow. The exact response depends on
    file-system state; we just pin that the guard didn't 403."""
    _scope_user_to_gaming(monkeypatch)

    app = _make_app()
    # Stub the YAML-load helpers so the test doesn't need real config
    # files on disk. The publishing-yaml resolver returns (None, None)
    # when no file is found — the handler then returns 404, which is
    # ≠ 403 (the only thing this pin asserts).
    with (
        patch("server.api.config_routes._resolve_publishing_yaml", return_value=(None, None)),
        patch("server.api.config_routes._config_dir_for_niche", return_value=None),
    ):
        with app.test_client() as c:
            r = c.patch(
                "/api/v1/config/auto-publish?niche_id=gaming",
                json={"rollout_pct": 0.5},
            )
    # Guard passed (would be 403 if it rejected). The 404 from the
    # missing-yaml downstream is fine — proves the guard let it through.
    assert r.status_code != 403


def test_unrestricted_admin_can_access_any_niche(monkeypatch):
    """Backward compat: admin operator with no allowlist can hit
    any niche's config endpoint."""
    monkeypatch.setenv("REVIEW_AUTH_USER", "admin")
    monkeypatch.delenv("REVIEW_NICHE_ALLOWLIST_ADMIN", raising=False)

    app = _make_app()
    with (
        patch("server.api.config_routes._resolve_publishing_yaml", return_value=(None, None)),
    ):
        with app.test_client() as c:
            r = c.patch(
                "/api/v1/config/auto-publish?niche_id=anime",
                json={"rollout_pct": 0.5},
            )
    # NOT 403 — admin unrestricted. The 404 from missing yaml is fine.
    assert r.status_code != 403
