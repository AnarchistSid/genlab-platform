"""PR #547 (2026-06-25): SR-F wire pass 8 — pipeline.py /trigger.

The express pipeline trigger endpoint takes ``niche_id`` in the
body (or defaults to ``"all"``) and kicks off a per-niche pipeline
run in a background thread. Two SR-F implications:

  1. niche_id in {gaming, sports, movies, anime, ai_creators}: same
     shape as config_routes.py — defer to _enforce_niche_keyed_allowlist
     so a restricted operator can only trigger their own niche.

  2. niche_id == "all": runs the pipeline for EVERY niche. For a
     restricted operator that's a cross-tenant leak (one of the 5
     sub-runs would be outside their scope). REJECT with 403 + a
     clear message telling them to trigger per-niche instead.

If these pins regress, a tenant-B operator could trigger
expensive pipeline runs for tenant-A's niche (or all niches at
once via "all") with one POST.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch


def test_source_pins_guard_on_pipeline_trigger():
    """The trigger handler must call _enforce_niche_keyed_allowlist
    for explicit niches AND reject 'all' for restricted operators."""
    import server.api.pipeline as mod

    src = Path(mod.__file__).read_text()
    # Both branches present:
    assert "_enforce_niche_keyed_allowlist(niche_id)" in src
    assert 'niche_id == "all"' in src
    # PR anchor for archaeology
    assert "PR #547" in src


def _scope_user_to_gaming(monkeypatch):
    """dotenv-override workaround from earlier SR-F tests."""
    monkeypatch.setenv("REVIEW_AUTH_USER", "gaming_op")
    monkeypatch.setenv("REVIEW_NICHE_ALLOWLIST_GAMING_OP", "gaming")
    monkeypatch.setattr(
        "server.auth.niche_allowlist._username_for_session",
        lambda: "gaming_op",
    )


def _make_app():
    from flask import Flask
    from server.api.pipeline import bp

    app = Flask(__name__)
    app.register_blueprint(bp)
    return app


def test_trigger_403_on_cross_tenant_niche(monkeypatch):
    """Gaming-scoped operator tries to trigger the anime pipeline → 403."""
    _scope_user_to_gaming(monkeypatch)

    app = _make_app()
    with app.test_client() as c:
        r = c.post("/api/v1/pipeline/trigger", json={"niche_id": "anime"})
    assert r.status_code == 403


def test_trigger_403_on_all_when_restricted(monkeypatch):
    """Gaming-scoped operator tries niche_id='all' → 403 with a
    clear message pointing at per-niche triggering."""
    _scope_user_to_gaming(monkeypatch)

    app = _make_app()
    with app.test_client() as c:
        r = c.post("/api/v1/pipeline/trigger", json={"niche_id": "all"})
    assert r.status_code == 403
    body = r.get_data(as_text=True)
    assert "all" in body
    assert "per-niche" in body.lower() or "SR-F" in body or "PR #547" in body


def test_trigger_403_when_default_all_with_restriction(monkeypatch):
    """Empty body → default niche_id='all'. Restricted operators
    must STILL hit the all-rejection branch — defaulting to 'all'
    doesn't bypass the guard."""
    _scope_user_to_gaming(monkeypatch)

    app = _make_app()
    with app.test_client() as c:
        # Empty JSON body → niche_id defaults to "all"
        r = c.post("/api/v1/pipeline/trigger", json={})
    assert r.status_code == 403


def test_trigger_all_proceeds_for_unrestricted_admin(monkeypatch):
    """Admin operator with no allowlist can trigger 'all'.
    Backward compat — the existing operator workflow keeps working."""
    monkeypatch.setenv("REVIEW_AUTH_USER", "admin")
    monkeypatch.delenv("REVIEW_NICHE_ALLOWLIST_ADMIN", raising=False)

    app = _make_app()
    # Stub the express-pipeline runner so we don't actually fire a
    # subprocess. The guard's pass-through is the assertion target.
    with patch("server.api.pipeline._run_unified_pipeline"):
        with app.test_client() as c:
            r = c.post("/api/v1/pipeline/trigger", json={"niche_id": "all"})

    # Status may be 200 (success) or 409 (lock already held in test env);
    # critical pin: NOT 403 → guard let it through.
    assert r.status_code != 403


def test_trigger_proceeds_for_same_tenant(monkeypatch):
    """Gaming operator triggers the gaming pipeline → guard passes."""
    _scope_user_to_gaming(monkeypatch)

    app = _make_app()
    with patch("server.api.pipeline._run_unified_pipeline"):
        with app.test_client() as c:
            r = c.post("/api/v1/pipeline/trigger", json={"niche_id": "gaming"})

    # NOT 403 — guard let same-tenant through
    assert r.status_code != 403
