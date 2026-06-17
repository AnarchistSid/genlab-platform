"""Tests for the M-19 youtube_channels CRUD API.

Covers:
  * GET happy path + auth-on-unknown-niche 404
  * POST add (happy path, validation failures, duplicate URL conflict,
    persistence via real ruamel round-trip)
  * DELETE remove (happy path, unknown URL 404, persistence)
  * CSRF enforcement on state-changing requests

Uses a tmp_path-scoped fake niche so writes are isolated and the
test's actual sources.yaml file gets read back to assert persistence.
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
def fake_niche_dir(tmp_path):
    """Create a fake niche config dir + sources.yaml with comments
    + a small youtube_channels list. Returns the config_dir path."""
    config_dir = tmp_path / "FakeNiche" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "sources.yaml").write_text(
        """# Fake-niche sources.yaml
# Test fixture for M-19 youtube_channels CRUD

youtube_channels:
  - url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCAAA"
    name: "Alpha Channel"
  - url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCBBB"
    name: "Beta Channel"

other_config:
  preserved: true
"""
    )
    return config_dir


def _get_csrf(client):
    return client.get("/api/csrf-token").get_json().get("csrf_token", "")


def _patch_resolver(config_dir):
    """Patch the niche resolver to point at our fake config dir."""
    return patch(
        "server.api.config_routes._config_dir_for_niche",
        return_value=config_dir,
    )


# ── GET ──────────────────────────────────────────────────────


class TestGetYoutubeChannels:
    def test_returns_existing_channels(self, client, fake_niche_dir):
        with _patch_resolver(fake_niche_dir):
            resp = client.get("/api/v1/config/sources/youtube-channels?niche_id=fake")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["niche_id"] == "fake"
        assert data["youtube_channels"] == [
            {
                "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCAAA",
                "name": "Alpha Channel",
            },
            {
                "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCBBB",
                "name": "Beta Channel",
            },
        ]

    def test_missing_niche_id_returns_400(self, client, fake_niche_dir):
        with _patch_resolver(fake_niche_dir):
            resp = client.get("/api/v1/config/sources/youtube-channels")
        assert resp.status_code == 400

    def test_unknown_niche_returns_404(self, client):
        with patch(
            "server.api.config_routes._config_dir_for_niche",
            return_value=None,
        ):
            resp = client.get("/api/v1/config/sources/youtube-channels?niche_id=bogus")
        assert resp.status_code == 404


# ── POST ─────────────────────────────────────────────────────


class TestAddYoutubeChannel:
    def test_appends_and_persists(self, client, fake_niche_dir):
        csrf = _get_csrf(client)
        with _patch_resolver(fake_niche_dir):
            resp = client.post(
                "/api/v1/config/sources/youtube-channels?niche_id=fake",
                json={
                    "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCNEW",
                    "name": "New Channel",
                    "_csrf_token": csrf,
                },
            )
        assert resp.status_code == 200
        # Real persistence — read the file back
        content = (fake_niche_dir / "sources.yaml").read_text()
        assert "UCNEW" in content
        assert "New Channel" in content
        # Comments survived
        assert "# Fake-niche sources.yaml" in content
        # Other keys preserved
        assert "preserved: true" in content

    def test_missing_url_returns_400(self, client, fake_niche_dir):
        csrf = _get_csrf(client)
        with _patch_resolver(fake_niche_dir):
            resp = client.post(
                "/api/v1/config/sources/youtube-channels?niche_id=fake",
                json={"name": "Missing URL", "_csrf_token": csrf},
            )
        assert resp.status_code == 400
        assert "url" in resp.get_json()["error"].lower()

    def test_missing_name_returns_400(self, client, fake_niche_dir):
        csrf = _get_csrf(client)
        with _patch_resolver(fake_niche_dir):
            resp = client.post(
                "/api/v1/config/sources/youtube-channels?niche_id=fake",
                json={
                    "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCXYZ",
                    "_csrf_token": csrf,
                },
            )
        assert resp.status_code == 400
        assert "name" in resp.get_json()["error"].lower()

    def test_non_youtube_host_rejected(self, client, fake_niche_dir):
        csrf = _get_csrf(client)
        with _patch_resolver(fake_niche_dir):
            resp = client.post(
                "/api/v1/config/sources/youtube-channels?niche_id=fake",
                json={
                    "url": "https://evil.com/feeds/videos.xml",
                    "name": "Evil",
                    "_csrf_token": csrf,
                },
            )
        assert resp.status_code == 400
        assert "youtube" in resp.get_json()["error"].lower()

    def test_non_http_scheme_rejected(self, client, fake_niche_dir):
        csrf = _get_csrf(client)
        with _patch_resolver(fake_niche_dir):
            resp = client.post(
                "/api/v1/config/sources/youtube-channels?niche_id=fake",
                json={
                    "url": "javascript:alert(1)",
                    "name": "XSS",
                    "_csrf_token": csrf,
                },
            )
        assert resp.status_code == 400

    def test_name_too_long_rejected(self, client, fake_niche_dir):
        csrf = _get_csrf(client)
        with _patch_resolver(fake_niche_dir):
            resp = client.post(
                "/api/v1/config/sources/youtube-channels?niche_id=fake",
                json={
                    "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCLONG",
                    "name": "x" * 200,
                    "_csrf_token": csrf,
                },
            )
        assert resp.status_code == 400

    def test_duplicate_url_returns_409(self, client, fake_niche_dir):
        csrf = _get_csrf(client)
        with _patch_resolver(fake_niche_dir):
            resp = client.post(
                "/api/v1/config/sources/youtube-channels?niche_id=fake",
                json={
                    # This URL is already in the fixture
                    "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCAAA",
                    "name": "Duplicate Try",
                    "_csrf_token": csrf,
                },
            )
        assert resp.status_code == 409

    def test_csrf_required(self, client, fake_niche_dir):
        with _patch_resolver(fake_niche_dir):
            resp = client.post(
                "/api/v1/config/sources/youtube-channels?niche_id=fake",
                json={
                    "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCNOCSRF",
                    "name": "No CSRF",
                },
            )
        assert resp.status_code == 403


# ── DELETE ───────────────────────────────────────────────────


class TestRemoveYoutubeChannel:
    def test_removes_and_persists(self, client, fake_niche_dir):
        csrf = _get_csrf(client)
        url_to_remove = "https://www.youtube.com/feeds/videos.xml?channel_id=UCAAA"
        with _patch_resolver(fake_niche_dir):
            resp = client.delete(
                f"/api/v1/config/sources/youtube-channels?niche_id=fake&url={url_to_remove}",
                headers={"X-CSRF-Token": csrf},
            )
        assert resp.status_code == 200
        # Alpha gone, Beta still present, comments preserved
        content = (fake_niche_dir / "sources.yaml").read_text()
        assert "UCAAA" not in content
        assert "UCBBB" in content
        assert "# Fake-niche sources.yaml" in content
        assert "preserved: true" in content

    def test_unknown_url_returns_404(self, client, fake_niche_dir):
        csrf = _get_csrf(client)
        with _patch_resolver(fake_niche_dir):
            resp = client.delete(
                "/api/v1/config/sources/youtube-channels?niche_id=fake&url=https://www.youtube.com/feeds/videos.xml?channel_id=UCMISSING",
                headers={"X-CSRF-Token": csrf},
            )
        assert resp.status_code == 404

    def test_missing_url_returns_400(self, client, fake_niche_dir):
        csrf = _get_csrf(client)
        with _patch_resolver(fake_niche_dir):
            resp = client.delete(
                "/api/v1/config/sources/youtube-channels?niche_id=fake",
                headers={"X-CSRF-Token": csrf},
            )
        assert resp.status_code == 400

    def test_csrf_required(self, client, fake_niche_dir):
        url_to_remove = "https://www.youtube.com/feeds/videos.xml?channel_id=UCAAA"
        with _patch_resolver(fake_niche_dir):
            resp = client.delete(
                f"/api/v1/config/sources/youtube-channels?niche_id=fake&url={url_to_remove}",
            )
        assert resp.status_code == 403


# ── Round-trip integrity (GET → POST → GET) ──────────────────


class TestRoundTrip:
    def test_get_after_post_reflects_addition(self, client, fake_niche_dir):
        csrf = _get_csrf(client)
        with _patch_resolver(fake_niche_dir):
            client.post(
                "/api/v1/config/sources/youtube-channels?niche_id=fake",
                json={
                    "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCROUND",
                    "name": "Round Trip",
                    "_csrf_token": csrf,
                },
            )
            resp = client.get("/api/v1/config/sources/youtube-channels?niche_id=fake")
        names = [c["name"] for c in resp.get_json()["data"]["youtube_channels"]]
        assert "Round Trip" in names

    def test_get_after_delete_reflects_removal(self, client, fake_niche_dir):
        csrf = _get_csrf(client)
        with _patch_resolver(fake_niche_dir):
            client.delete(
                "/api/v1/config/sources/youtube-channels?niche_id=fake&url=https://www.youtube.com/feeds/videos.xml?channel_id=UCAAA",
                headers={"X-CSRF-Token": csrf},
            )
            resp = client.get("/api/v1/config/sources/youtube-channels?niche_id=fake")
        urls = [c["url"] for c in resp.get_json()["data"]["youtube_channels"]]
        assert all("UCAAA" not in u for u in urls)


# ── Validator tightening (post-Wave-4 audit) ─────────────────────────


class TestValidatorTightening:
    """The post-Wave-4 audit (2026-06-17) flagged the original
    validator as too loose — it accepted youtu.be (video URLs),
    /watch (video pages), /results (search pages), and the homepage.
    All of those silently break the pipeline. Tighten to channel-
    flavoured URLs only."""

    def test_youtu_be_short_url_rejected(self, client, fake_niche_dir):
        csrf = _get_csrf(client)
        with _patch_resolver(fake_niche_dir):
            resp = client.post(
                "/api/v1/config/sources/youtube-channels?niche_id=fake",
                json={
                    "url": "https://youtu.be/dQw4w9WgXcQ",  # video short URL
                    "name": "Video Not Channel",
                    "_csrf_token": csrf,
                },
            )
        assert resp.status_code == 400
        # Error message should mention either host (rejected) or path
        # (also rejected) — either is acceptable signal to operator
        err = resp.get_json()["error"].lower()
        assert "host" in err or "youtu.be" in err

    def test_watch_url_rejected(self, client, fake_niche_dir):
        csrf = _get_csrf(client)
        with _patch_resolver(fake_niche_dir):
            resp = client.post(
                "/api/v1/config/sources/youtube-channels?niche_id=fake",
                json={
                    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    "name": "Video",
                    "_csrf_token": csrf,
                },
            )
        assert resp.status_code == 400
        assert "path" in resp.get_json()["error"].lower()

    def test_search_url_rejected(self, client, fake_niche_dir):
        csrf = _get_csrf(client)
        with _patch_resolver(fake_niche_dir):
            resp = client.post(
                "/api/v1/config/sources/youtube-channels?niche_id=fake",
                json={
                    "url": "https://www.youtube.com/results?search_query=ai",
                    "name": "Search Results",
                    "_csrf_token": csrf,
                },
            )
        assert resp.status_code == 400

    def test_homepage_rejected(self, client, fake_niche_dir):
        csrf = _get_csrf(client)
        with _patch_resolver(fake_niche_dir):
            resp = client.post(
                "/api/v1/config/sources/youtube-channels?niche_id=fake",
                json={
                    "url": "https://www.youtube.com/",
                    "name": "Homepage",
                    "_csrf_token": csrf,
                },
            )
        assert resp.status_code == 400

    def test_channel_id_path_accepted(self, client, fake_niche_dir):
        csrf = _get_csrf(client)
        with _patch_resolver(fake_niche_dir):
            resp = client.post(
                "/api/v1/config/sources/youtube-channels?niche_id=fake",
                json={
                    "url": "https://www.youtube.com/channel/UCBR8-60-B28hp2BmDPdntcQ",
                    "name": "Channel Page",
                    "_csrf_token": csrf,
                },
            )
        assert resp.status_code == 200

    def test_at_handle_accepted(self, client, fake_niche_dir):
        csrf = _get_csrf(client)
        with _patch_resolver(fake_niche_dir):
            resp = client.post(
                "/api/v1/config/sources/youtube-channels?niche_id=fake",
                json={
                    "url": "https://www.youtube.com/@evolvingai",
                    "name": "Handle Page",
                    "_csrf_token": csrf,
                },
            )
        assert resp.status_code == 200

    def test_legacy_user_path_accepted(self, client, fake_niche_dir):
        csrf = _get_csrf(client)
        with _patch_resolver(fake_niche_dir):
            resp = client.post(
                "/api/v1/config/sources/youtube-channels?niche_id=fake",
                json={
                    "url": "https://www.youtube.com/user/somebody",
                    "name": "Legacy User",
                    "_csrf_token": csrf,
                },
            )
        assert resp.status_code == 200

    def test_legacy_custom_c_path_accepted(self, client, fake_niche_dir):
        csrf = _get_csrf(client)
        with _patch_resolver(fake_niche_dir):
            resp = client.post(
                "/api/v1/config/sources/youtube-channels?niche_id=fake",
                json={
                    "url": "https://www.youtube.com/c/something",
                    "name": "Legacy Custom",
                    "_csrf_token": csrf,
                },
            )
        assert resp.status_code == 200


# ── Concurrent-write protection (file lock) ──────────────────────────


class TestConcurrentWriteProtection:
    """The audit-confirmed race: two simultaneous POSTs both read the
    same channels list, both append, both write — second wins, first
    silently lost. fcntl.flock serializes them so the second blocks
    until the first releases, and the second's add is appended on TOP
    of the first's."""

    def test_two_adds_both_persist(self, client, fake_niche_dir):
        """End-to-end: serial calls under the lock should BOTH persist,
        not the second-overwrites-first behaviour. This pins the
        invariant; the lock makes it work even under genuine
        concurrency."""
        csrf = _get_csrf(client)
        with _patch_resolver(fake_niche_dir):
            r1 = client.post(
                "/api/v1/config/sources/youtube-channels?niche_id=fake",
                json={
                    "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCFIRST",
                    "name": "First",
                    "_csrf_token": csrf,
                },
            )
            r2 = client.post(
                "/api/v1/config/sources/youtube-channels?niche_id=fake",
                json={
                    "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCSECOND",
                    "name": "Second",
                    "_csrf_token": csrf,
                },
            )
        assert r1.status_code == 200
        assert r2.status_code == 200

        content = (fake_niche_dir / "sources.yaml").read_text()
        assert "UCFIRST" in content, "first add must persist"
        assert "UCSECOND" in content, "second add must persist"

    def test_lock_file_created_alongside_yaml(self, client, fake_niche_dir):
        """The fcntl lock uses a sidecar ``.lock`` file. Confirm it's
        created in the same dir + has the expected name. The convention
        matters because operators may need to clean it up manually if a
        crashed worker leaves it behind (though flock on Linux releases
        on process death)."""
        csrf = _get_csrf(client)
        with _patch_resolver(fake_niche_dir):
            client.post(
                "/api/v1/config/sources/youtube-channels?niche_id=fake",
                json={
                    "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCLOCK",
                    "name": "Lock Probe",
                    "_csrf_token": csrf,
                },
            )
        lock_path = fake_niche_dir / "sources.yaml.lock"
        assert lock_path.exists(), "fcntl lock sidecar must be created"

    def test_delete_under_lock_blocks_concurrent_add(self, client, fake_niche_dir):
        """Verify the lock instance is reentered by both paths — the
        DELETE handler must use the same lock as POST so an add can't
        race with a remove. Smoke-test by sequentially issuing
        DELETE then POST on the SAME niche."""
        csrf = _get_csrf(client)
        url_to_remove = "https://www.youtube.com/feeds/videos.xml?channel_id=UCAAA"
        with _patch_resolver(fake_niche_dir):
            d = client.delete(
                f"/api/v1/config/sources/youtube-channels?niche_id=fake&url={url_to_remove}",
                headers={"X-CSRF-Token": csrf},
            )
            p = client.post(
                "/api/v1/config/sources/youtube-channels?niche_id=fake",
                json={
                    "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCAFTER",
                    "name": "After Delete",
                    "_csrf_token": csrf,
                },
            )
        assert d.status_code == 200
        assert p.status_code == 200
        content = (fake_niche_dir / "sources.yaml").read_text()
        assert "UCAAA" not in content
        assert "UCAFTER" in content
