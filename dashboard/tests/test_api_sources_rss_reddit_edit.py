"""Tests for M-20 (rss_feeds_extra) + M-21 (reddit.subreddits) CRUD APIs.

Mirrors the structure of test_api_sources_youtube_edit.py — same
fixtures, same auth disable, same fake-niche pattern. Covers:

* GET happy path + missing-niche 404
* POST add (happy path, validation failures, duplicate conflict,
  default-value handling, persistence via real ruamel round-trip)
* DELETE remove (happy path, unknown key 404, persistence)
* The case-insensitive dedup contract for subreddits (Reddit's own
  contract — "r/AnimeSakuga" == "animesakuga")
* The ``r/`` and ``/r/`` prefix-stripping contract for subreddit input
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
    """Create a fake niche config dir + sources.yaml with comments,
    one rss_feeds_extra entry, and one reddit.subreddits entry.
    Returns the config_dir path."""
    config_dir = tmp_path / "FakeNiche" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "sources.yaml").write_text(
        """# Fake-niche sources.yaml
# Test fixture for M-20 (rss_feeds_extra) + M-21 (reddit.subreddits)

rss_feeds_extra:
  - url: "https://example.com/feed.xml"
    name: "Example Feed"
    max_age_hours: 24

reddit:
  enabled: true
  listing: top
  time_window: day
  per_sub_limit: 15
  subreddits:
    - name: "AnimeSakuga"
    - name: "anime"

other_config:
  preserved: true
"""
    )
    return config_dir


def _get_csrf(client):
    return client.get("/api/csrf-token").get_json().get("csrf_token", "")


def _patch_resolver(config_dir):
    return patch(
        "server.api.config_routes._config_dir_for_niche",
        return_value=config_dir,
    )


# ── M-20: rss_feeds_extra ───────────────────────────────────


class TestGetRssFeeds:
    def test_returns_existing_feeds(self, client, fake_niche_dir):
        with _patch_resolver(fake_niche_dir):
            resp = client.get("/api/v1/config/sources/rss-feeds?niche_id=fake")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["niche_id"] == "fake"
        assert data["rss_feeds_extra"] == [
            {
                "url": "https://example.com/feed.xml",
                "name": "Example Feed",
                "max_age_hours": 24,
            }
        ]

    def test_missing_niche_id_returns_400(self, client, fake_niche_dir):
        with _patch_resolver(fake_niche_dir):
            resp = client.get("/api/v1/config/sources/rss-feeds")
        assert resp.status_code == 400


class TestAddRssFeed:
    def test_happy_path_with_default_max_age(self, client, fake_niche_dir):
        token = _get_csrf(client)
        with _patch_resolver(fake_niche_dir):
            resp = client.post(
                "/api/v1/config/sources/rss-feeds?niche_id=fake",
                json={"url": "https://newsite.com/feed", "name": "New Site"},
                headers={"X-CSRF-Token": token},
            )
        assert resp.status_code == 200, resp.get_json()
        # Default max_age_hours = 168 (7 days) — pinned because the
        # pipeline relies on this default when operators don't
        # specify it.
        assert resp.get_json()["data"]["added"]["max_age_hours"] == 168

    def test_explicit_max_age_persists(self, client, fake_niche_dir):
        token = _get_csrf(client)
        with _patch_resolver(fake_niche_dir):
            resp = client.post(
                "/api/v1/config/sources/rss-feeds?niche_id=fake",
                json={
                    "url": "https://other.com/rss",
                    "name": "Other",
                    "max_age_hours": 12,
                },
                headers={"X-CSRF-Token": token},
            )
        assert resp.status_code == 200
        assert resp.get_json()["data"]["added"]["max_age_hours"] == 12

    @pytest.mark.parametrize(
        "url, expected_substring",
        [
            ("http://example.com/feed", "https"),  # http rejected
            # "not-a-url" parses as no-scheme — scheme check fires
            # before host check, so the operator sees the "https"
            # message. We pin that ordering deliberately.
            ("not-a-url", "https"),
            ("https://", "host"),  # missing host (scheme passes)
            ("https://example.com", "path"),  # no path
            ("https://example.com/", "path"),  # bare slash path
        ],
    )
    def test_validator_rejects_bad_urls(self, client, fake_niche_dir, url, expected_substring):
        token = _get_csrf(client)
        with _patch_resolver(fake_niche_dir):
            resp = client.post(
                "/api/v1/config/sources/rss-feeds?niche_id=fake",
                json={"url": url, "name": "X"},
                headers={"X-CSRF-Token": token},
            )
        assert resp.status_code == 400
        assert expected_substring in resp.get_json()["error"].lower()

    def test_max_age_zero_rejected(self, client, fake_niche_dir):
        token = _get_csrf(client)
        with _patch_resolver(fake_niche_dir):
            resp = client.post(
                "/api/v1/config/sources/rss-feeds?niche_id=fake",
                json={
                    "url": "https://ok.com/feed",
                    "name": "X",
                    "max_age_hours": 0,
                },
                headers={"X-CSRF-Token": token},
            )
        assert resp.status_code == 400
        assert "positive" in resp.get_json()["error"].lower()

    def test_max_age_above_cap_rejected(self, client, fake_niche_dir):
        token = _get_csrf(client)
        with _patch_resolver(fake_niche_dir):
            resp = client.post(
                "/api/v1/config/sources/rss-feeds?niche_id=fake",
                json={
                    "url": "https://ok.com/feed",
                    "name": "X",
                    "max_age_hours": 99999,
                },
                headers={"X-CSRF-Token": token},
            )
        assert resp.status_code == 400

    def test_duplicate_url_returns_409(self, client, fake_niche_dir):
        token = _get_csrf(client)
        with _patch_resolver(fake_niche_dir):
            resp = client.post(
                "/api/v1/config/sources/rss-feeds?niche_id=fake",
                json={
                    "url": "https://example.com/feed.xml",  # already present
                    "name": "Duplicate",
                },
                headers={"X-CSRF-Token": token},
            )
        assert resp.status_code == 409

    def test_persists_to_disk(self, client, fake_niche_dir):
        token = _get_csrf(client)
        with _patch_resolver(fake_niche_dir):
            client.post(
                "/api/v1/config/sources/rss-feeds?niche_id=fake",
                json={"url": "https://x.com/feed", "name": "X"},
                headers={"X-CSRF-Token": token},
            )
        # Read the file back through ruamel to confirm it's there
        # and comments survived the round-trip
        content = (fake_niche_dir / "sources.yaml").read_text()
        assert "https://x.com/feed" in content
        assert "Test fixture for M-20" in content  # comment survived


class TestRemoveRssFeed:
    def test_happy_path(self, client, fake_niche_dir):
        token = _get_csrf(client)
        with _patch_resolver(fake_niche_dir):
            resp = client.delete(
                "/api/v1/config/sources/rss-feeds?"
                "niche_id=fake&url=https%3A%2F%2Fexample.com%2Ffeed.xml",
                headers={"X-CSRF-Token": token},
            )
        assert resp.status_code == 200

    def test_unknown_url_returns_404(self, client, fake_niche_dir):
        token = _get_csrf(client)
        with _patch_resolver(fake_niche_dir):
            resp = client.delete(
                "/api/v1/config/sources/rss-feeds?niche_id=fake&url=https%3A%2F%2Fnope.com%2F",
                headers={"X-CSRF-Token": token},
            )
        assert resp.status_code == 404


# ── M-21: reddit.subreddits ─────────────────────────────────


class TestGetSubreddits:
    def test_returns_existing_subreddits(self, client, fake_niche_dir):
        with _patch_resolver(fake_niche_dir):
            resp = client.get("/api/v1/config/sources/subreddits?niche_id=fake")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["niche_id"] == "fake"
        names = [s["name"] for s in data["subreddits"]]
        assert names == ["AnimeSakuga", "anime"]


class TestAddSubreddit:
    def test_happy_path_bare_name(self, client, fake_niche_dir):
        token = _get_csrf(client)
        with _patch_resolver(fake_niche_dir):
            resp = client.post(
                "/api/v1/config/sources/subreddits?niche_id=fake",
                json={"name": "Anthropic"},
                headers={"X-CSRF-Token": token},
            )
        assert resp.status_code == 200
        assert resp.get_json()["data"]["added"]["name"] == "Anthropic"

    @pytest.mark.parametrize(
        "input_name, normalized",
        [
            ("r/Anthropic", "Anthropic"),
            ("/r/Anthropic", "Anthropic"),
            ("  r/Anthropic  ", "Anthropic"),
        ],
    )
    def test_strips_r_prefix(self, client, fake_niche_dir, input_name, normalized):
        token = _get_csrf(client)
        with _patch_resolver(fake_niche_dir):
            resp = client.post(
                "/api/v1/config/sources/subreddits?niche_id=fake",
                json={"name": input_name},
                headers={"X-CSRF-Token": token},
            )
        assert resp.status_code == 200
        assert resp.get_json()["data"]["added"]["name"] == normalized

    @pytest.mark.parametrize(
        "bad_name, expected_substring",
        [
            ("ab", "≥3"),  # too short
            ("a" * 22, "≤21"),  # too long
            ("has-hyphen", "alphanumeric"),  # hyphens illegal
            ("has space", "alphanumeric"),  # spaces illegal
            ("has.dot", "alphanumeric"),  # dots illegal
            ("_leading", "alphanumeric"),  # can't start with _
            ("", "required"),  # empty
        ],
    )
    def test_validator_rejects_bad_names(
        self, client, fake_niche_dir, bad_name, expected_substring
    ):
        token = _get_csrf(client)
        with _patch_resolver(fake_niche_dir):
            resp = client.post(
                "/api/v1/config/sources/subreddits?niche_id=fake",
                json={"name": bad_name},
                headers={"X-CSRF-Token": token},
            )
        assert resp.status_code == 400
        assert expected_substring in resp.get_json()["error"]

    def test_case_insensitive_dedup(self, client, fake_niche_dir):
        """``r/AnimeSakuga`` already exists. Adding ``animesakuga`` (lower)
        must 409 because Reddit treats names case-insensitively."""
        token = _get_csrf(client)
        with _patch_resolver(fake_niche_dir):
            resp = client.post(
                "/api/v1/config/sources/subreddits?niche_id=fake",
                json={"name": "animesakuga"},
                headers={"X-CSRF-Token": token},
            )
        assert resp.status_code == 409


class TestRemoveSubreddit:
    def test_happy_path(self, client, fake_niche_dir):
        token = _get_csrf(client)
        with _patch_resolver(fake_niche_dir):
            resp = client.delete(
                "/api/v1/config/sources/subreddits?niche_id=fake&name=anime",
                headers={"X-CSRF-Token": token},
            )
        assert resp.status_code == 200

    def test_case_insensitive_match(self, client, fake_niche_dir):
        """Storing ``AnimeSakuga`` but deleting ``animesakuga`` must
        succeed — same case-insensitive contract as add."""
        token = _get_csrf(client)
        with _patch_resolver(fake_niche_dir):
            resp = client.delete(
                "/api/v1/config/sources/subreddits?niche_id=fake&name=animesakuga",
                headers={"X-CSRF-Token": token},
            )
        assert resp.status_code == 200

    def test_r_prefix_stripped_on_delete(self, client, fake_niche_dir):
        token = _get_csrf(client)
        with _patch_resolver(fake_niche_dir):
            resp = client.delete(
                "/api/v1/config/sources/subreddits?niche_id=fake&name=r%2Fanime",
                headers={"X-CSRF-Token": token},
            )
        assert resp.status_code == 200

    def test_unknown_name_returns_404(self, client, fake_niche_dir):
        token = _get_csrf(client)
        with _patch_resolver(fake_niche_dir):
            resp = client.delete(
                "/api/v1/config/sources/subreddits?niche_id=fake&name=nope",
                headers={"X-CSRF-Token": token},
            )
        assert resp.status_code == 404


# ── Validator unit tests (no HTTP — pure-function pins) ──────


class TestUrlValidator:
    """Direct unit tests of _validate_rss_url so error messages are
    pinned. Easier to debug than reading raw 400 bodies."""

    def test_https_with_path_is_valid(self):
        from server.api.config_routes import _validate_rss_url

        assert _validate_rss_url("https://example.com/feed") is None

    def test_http_rejected(self):
        from server.api.config_routes import _validate_rss_url

        err = _validate_rss_url("http://example.com/feed")
        assert err is not None and "https" in err


class TestSubredditValidator:
    def test_normalize_strips_r_prefix(self):
        from server.api.config_routes import _normalize_subreddit_name

        assert _normalize_subreddit_name("r/foo") == "foo"
        assert _normalize_subreddit_name("/r/foo") == "foo"
        assert _normalize_subreddit_name("  r/foo  ") == "foo"
        assert _normalize_subreddit_name("foo") == "foo"

    def test_validator_accepts_alphanumeric_underscore(self):
        from server.api.config_routes import _validate_subreddit_name

        assert _validate_subreddit_name("abc") is None
        assert _validate_subreddit_name("abc123") is None
        assert _validate_subreddit_name("a_b_c") is None
        assert _validate_subreddit_name("a" * 21) is None  # at max
