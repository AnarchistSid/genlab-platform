"""Tests for the AUTO #2 ``auto_publish.rollout_pct`` slider endpoints.

Pins:
* GET happy path (existing block + default field values)
* GET when auto_publish missing → defaults surfaced
* GET when niche unknown → 404
* PATCH happy path (in-range value persists via real ruamel round-trip
  and comments survive)
* PATCH out-of-range / non-numeric → 400 with debuggable messages
* PATCH when auto_publish block missing → creates safe defaults
  (enabled=false) so the operator's first slide doesn't accidentally
  flip enabled on
* The contract that ONLY rollout_pct is writeable — sending
  ``enabled: true`` in the body has no effect (other fields ignored
  silently)

Reuses the same auth-disabled + tmp_path fixture pattern as
test_api_sources_youtube_edit.py / test_api_sources_rss_reddit_edit.py.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import server.review_server as review_server_module
from server.review_server import app


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    monkeypatch.setattr(review_server_module, "_AUTH_ENABLED", False)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def fake_publishing_path(tmp_path):
    """Create a fake publishing.yaml with a fully-populated auto_publish
    block + a multi-line comment above rollout_pct so the round-trip
    test can verify the comment survives."""
    publishing_yaml = tmp_path / "publishing.yaml"
    publishing_yaml.write_text(
        """# Fake-niche publishing.yaml
# Test fixture for AUTO #2 rollout_pct slider

auto_publish:
  enabled: false
  min_confidence: 0.85
  max_approvals_per_pass: 3
  # W4.3 graduated rollout — should survive round-trip
  rollout_pct: 0.5

other_block:
  preserved: true
"""
    )
    return publishing_yaml


def _get_csrf(client):
    return client.get("/api/csrf-token").get_json().get("csrf_token", "")


def _patch_resolver(publishing_path):
    """Patch the publishing.yaml resolver to point at our fixture."""
    return patch(
        "server.api.config_routes._resolve_publishing_yaml",
        return_value=(publishing_path, "FakeFolder"),
    )


# ── GET ──────────────────────────────────────────────────────


class TestGetAutoPublish:
    def test_returns_all_four_fields(self, client, fake_publishing_path):
        with _patch_resolver(fake_publishing_path):
            resp = client.get("/api/v1/config/auto-publish?niche_id=fake")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["niche_id"] == "fake"
        assert data["auto_publish"] == {
            "enabled": False,
            "min_confidence": 0.85,
            "max_approvals_per_pass": 3,
            "rollout_pct": 0.5,
        }

    def test_missing_niche_id_returns_400(self, client):
        resp = client.get("/api/v1/config/auto-publish")
        assert resp.status_code == 400

    def test_unknown_niche_returns_404(self, client):
        with patch(
            "server.api.config_routes._resolve_publishing_yaml",
            return_value=(None, None),
        ):
            resp = client.get("/api/v1/config/auto-publish?niche_id=ghost")
        assert resp.status_code == 404

    def test_missing_block_surfaces_defaults(self, client, tmp_path):
        """publishing.yaml exists but has no auto_publish block — GET
        returns the default values matching auto_approver's policy
        defaults so the slider has something sensible to render."""
        empty = tmp_path / "publishing.yaml"
        empty.write_text("other_block:\n  irrelevant: true\n")
        with patch(
            "server.api.config_routes._resolve_publishing_yaml",
            return_value=(empty, "FakeFolder"),
        ):
            resp = client.get("/api/v1/config/auto-publish?niche_id=fake")
        assert resp.status_code == 200
        block = resp.get_json()["data"]["auto_publish"]
        assert block["enabled"] is False
        assert block["rollout_pct"] == 1.0


# ── PATCH ────────────────────────────────────────────────────


class TestPatchAutoPublish:
    def test_happy_path_persists_and_preserves_comments(
        self, client, fake_publishing_path
    ):
        token = _get_csrf(client)
        with _patch_resolver(fake_publishing_path):
            resp = client.patch(
                "/api/v1/config/auto-publish?niche_id=fake",
                json={"rollout_pct": 0.25},
                headers={"X-CSRF-Token": token},
            )
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()["data"]["updated"]["rollout_pct"] == 0.25
        # Read file back — verify the value persisted AND the comments
        # survived the ruamel round-trip.
        content = fake_publishing_path.read_text()
        assert "rollout_pct: 0.25" in content
        assert "Test fixture for AUTO #2 rollout_pct slider" in content
        assert "W4.3 graduated rollout" in content
        assert "other_block:" in content  # sibling block untouched

    @pytest.mark.parametrize(
        "value, expected_substring",
        [
            (-0.01, "[0.0, 1.0]"),
            (1.01, "[0.0, 1.0]"),
            (50, "[0.0, 1.0]"),       # common typo: 50 meant 50%
            ("not-a-number", "number"),
            (None, "required"),
        ],
    )
    def test_validator_rejects_bad_values(
        self, client, fake_publishing_path, value, expected_substring
    ):
        token = _get_csrf(client)
        body = {} if value is None else {"rollout_pct": value}
        with _patch_resolver(fake_publishing_path):
            resp = client.patch(
                "/api/v1/config/auto-publish?niche_id=fake",
                json=body,
                headers={"X-CSRF-Token": token},
            )
        assert resp.status_code == 400
        assert expected_substring in resp.get_json()["error"]

    def test_boundary_values_accepted(self, client, fake_publishing_path):
        """0.0 and 1.0 are both legal — pin them so the inclusive
        range doesn't drift to exclusive in a future refactor."""
        token = _get_csrf(client)
        for boundary in (0.0, 1.0):
            with _patch_resolver(fake_publishing_path):
                resp = client.patch(
                    "/api/v1/config/auto-publish?niche_id=fake",
                    json={"rollout_pct": boundary},
                    headers={"X-CSRF-Token": token},
                )
            assert resp.status_code == 200, (boundary, resp.get_json())

    def test_creates_safe_defaults_when_block_missing(self, client, tmp_path):
        """If the operator slides for a niche that has no auto_publish
        block yet, we create the block with enabled=false so the slide
        doesn't accidentally flip on the gate. Pin this — the safe-
        default contract is core to the AUTO #2 rollout runbook."""
        empty = tmp_path / "publishing.yaml"
        empty.write_text("other_block:\n  irrelevant: true\n")
        token = _get_csrf(client)
        with patch(
            "server.api.config_routes._resolve_publishing_yaml",
            return_value=(empty, "FakeFolder"),
        ):
            resp = client.patch(
                "/api/v1/config/auto-publish?niche_id=fake",
                json={"rollout_pct": 0.3},
                headers={"X-CSRF-Token": token},
            )
        assert resp.status_code == 200
        content = empty.read_text()
        # The block exists with rollout_pct AND enabled stays false
        assert "rollout_pct: 0.3" in content
        assert "enabled: false" in content

    def test_only_rollout_pct_writeable(self, client, fake_publishing_path):
        """Sending ``enabled: true`` alongside rollout_pct must NOT
        flip enabled. This is the calibrated-opt-in contract per the
        AUTO #2 runbook §4-5: enabled flip is git-commit only."""
        token = _get_csrf(client)
        with _patch_resolver(fake_publishing_path):
            client.patch(
                "/api/v1/config/auto-publish?niche_id=fake",
                json={"rollout_pct": 0.42, "enabled": True},
                headers={"X-CSRF-Token": token},
            )
        # Re-read the persisted yaml — enabled must STILL be false
        content = fake_publishing_path.read_text()
        assert "enabled: false" in content
        assert "rollout_pct: 0.42" in content

    def test_unknown_niche_returns_404(self, client):
        token = _get_csrf(client)
        with patch(
            "server.api.config_routes._resolve_publishing_yaml",
            return_value=(None, None),
        ):
            resp = client.patch(
                "/api/v1/config/auto-publish?niche_id=ghost",
                json={"rollout_pct": 0.5},
                headers={"X-CSRF-Token": token},
            )
        assert resp.status_code == 404
