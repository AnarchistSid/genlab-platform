"""PR #548 (2026-06-25): SR-F wire pass 9 — monetisation /send-deal
maps channel slug → niche_id and applies the per-tenant guard.

The /send-deal endpoint blasts a notification email to every
subscriber of a channel. Pre-PR-548 a tenant-B operator could
send a deal to tenant-A's subscriber list by passing the right
channel slug — a real cross-tenant write that could damage
reputation or violate per-tenant marketing-permission scope.

This PR threads the channel slug through the canonical
_CHANNEL_TO_NICHE mapping from links.py (same source of truth
used by /links/subscribe), then defers to
_enforce_niche_keyed_allowlist for the actual check. No new
mapping — just plumbing.

If these pins regress, the per-tenant marketing isolation breaks:
gaming operator can send anime deals, etc.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch


def test_source_pin_uses_canonical_channel_to_niche_mapping():
    """The handler must import _CHANNEL_TO_NICHE from links.py —
    don't duplicate the mapping locally. Drift between two copies
    is exactly the bug class this avoids."""
    import server.api.monetisation as mod

    src = Path(mod.__file__).read_text()
    assert "from server.api.links import _CHANNEL_TO_NICHE" in src
    # And the guard call uses the MAPPED niche, not the raw channel
    assert "_enforce_niche_keyed_allowlist(_mapped_niche)" in src
    assert "PR #548" in src


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
    from server.api.monetisation import bp

    app = Flask(__name__)
    app.register_blueprint(bp)
    return app


def test_send_deal_403_on_cross_tenant_channel(monkeypatch):
    """Gaming-scoped operator tries to email the SpliceReel (movies)
    audience → 403. The email batch MUST NOT fire."""
    _scope_user_to_gaming(monkeypatch)

    app = _make_app()
    with patch("server.core.email_sender.send_batch_deals") as mock_send:
        with app.test_client() as c:
            r = c.post(
                "/api/v1/monetisation/send-deal",
                json={
                    "channel": "splicereel",
                    "product_name": "Movie tie-in",
                    "product_url": "https://example.com/x",
                },
            )
    assert r.status_code == 403
    mock_send.assert_not_called()


def test_send_deal_403_for_each_unmapped_niche(monkeypatch):
    """The 4 channels mapped to niches OUTSIDE gaming should ALL 403.
    Pin the whole cross-tenant set so a future _CHANNEL_TO_NICHE
    drift can't accidentally let a niche slip through."""
    _scope_user_to_gaming(monkeypatch)

    app = _make_app()
    other_channels = ["blackboxbrief", "clutchwire", "splicereel", "framedrift"]
    with patch("server.core.email_sender.send_batch_deals") as mock_send:
        with app.test_client() as c:
            for ch in other_channels:
                r = c.post(
                    "/api/v1/monetisation/send-deal",
                    json={
                        "channel": ch,
                        "product_name": "x",
                        "product_url": "https://example.com/x",
                    },
                )
                assert r.status_code == 403, (
                    f"channel={ch} should be 403 for gaming-scoped operator"
                )
    mock_send.assert_not_called()


def test_send_deal_proceeds_for_same_tenant(monkeypatch):
    """Gaming operator sends a deal to CriticalRush (gaming) → guard
    passes. Email batch IS called (we stub send + is_configured)."""
    _scope_user_to_gaming(monkeypatch)

    app = _make_app()
    with (
        patch("server.core.email_sender.is_configured", return_value=True),
        patch("server.core.email_sender.send_batch_deals", return_value=5),
    ):
        with app.test_client() as c:
            r = c.post(
                "/api/v1/monetisation/send-deal",
                json={
                    "channel": "criticalrush",
                    "product_name": "Gaming chair",
                    "product_url": "https://example.com/chair",
                },
            )
    assert r.status_code == 200
    body = r.get_json()
    assert body["data"]["sent"] == 5


def test_send_deal_admin_can_target_any_channel(monkeypatch):
    """Backward compat: admin operator with no allowlist can email
    any channel. The existing single-operator workflow is preserved."""
    monkeypatch.setenv("REVIEW_AUTH_USER", "admin")
    monkeypatch.delenv("REVIEW_NICHE_ALLOWLIST_ADMIN", raising=False)

    app = _make_app()
    with (
        patch("server.core.email_sender.is_configured", return_value=True),
        patch("server.core.email_sender.send_batch_deals", return_value=42),
    ):
        with app.test_client() as c:
            r = c.post(
                "/api/v1/monetisation/send-deal",
                json={
                    "channel": "framedrift",
                    "product_name": "Anime poster",
                    "product_url": "https://example.com/poster",
                },
            )
    assert r.status_code == 200


def test_invalid_channel_400_predates_guard(monkeypatch):
    """The 'invalid channel' 400 check must still run before the
    SR-F guard — operators with allowlists should get a clear 400
    on a typo'd channel, not a 403 that's confusingly wrong."""
    _scope_user_to_gaming(monkeypatch)

    app = _make_app()
    with app.test_client() as c:
        r = c.post(
            "/api/v1/monetisation/send-deal",
            json={"channel": "totally-fake-channel"},
        )
    assert r.status_code == 400
    body = r.get_data(as_text=True)
    assert "Invalid channel" in body
