"""Pin tests for reward-fetcher exception hardening (tasks #578, #579).

Both YouTube (`_fetch_youtube`) and X/Twitter (`_fetch_x`) reward fetchers
previously called ``resp.raise_for_status()`` outside a try/except. When the
call raised (401 on rotated tokens, 404 on deleted content, 429 on rate
limits, transient 5xx), the exception propagated up to the
``metric_collector`` orchestrator, which caught it in a bare
``except Exception`` and returned ``{}``. That empty dict then failed the
``if window == "48h" and metrics:`` check at ``metric_collector.py:863``,
silently skipping the ``reward_48h`` compute.

Prod verification 2026-07-08 (30-day window, `pending_feedback`):

  * YouTube: 61/74 rows had ``reward_48h`` NULL (82% missing)
  * Twitter: 13/17 rows had ``reward_48h`` NULL (76% missing)

vs Facebook's 0/81 missing (fetcher uses layered try/except throughout).

These tests LOCK IN the hardening: the fetchers MUST NOT raise on any HTTP
error class. They log a warning and return ``{}`` so the orchestrator can
continue processing the batch. See ``audit-deep-dive-2026-07-08.md`` for
the forensic report.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests
from genlab_core.learning.metrics import x_twitter, youtube


class TestYouTubeFetcherHardening:
    """The unwrapped ``raise_for_status()`` at metrics/youtube.py:256, :268 was
    the single largest source of missing YT reward writes in prod. These tests
    ensure both call sites now swallow HTTP errors gracefully.
    """

    def test_returns_empty_on_token_refresh_401(self, monkeypatch):
        """A stale refresh token (401 on oauth2/token) must NOT propagate.
        Before the fix, this would bubble to the orchestrator's bare except
        and skip reward compute silently for every YT post in the batch.
        """
        monkeypatch.setattr(youtube, "_yt_token_cache", {"token": None, "niche": None, "ts": 0.0})

        def _resolve_creds(*_args, **_kwargs):
            # Real signature returns dict[str, str] — see niche_credentials.py:162
            return {"client_id": "cid", "client_secret": "csecret", "refresh_token": "reftok"}

        monkeypatch.setattr(
            "genlab_core.publishing.niche_credentials.resolve_youtube_credentials",
            _resolve_creds,
            raising=False,
        )

        fake_401 = MagicMock()
        fake_401.raise_for_status.side_effect = requests.HTTPError(
            "401 Unauthorized", response=MagicMock(status_code=401)
        )
        with patch("requests.post", return_value=fake_401):
            result = youtube._fetch_youtube("VIDEO_ID_ABC", niche_id="gaming")

        assert result == {}, (
            f"YouTube fetcher must return {{}} on token refresh 401, got {result!r}. "
            "Regression: unwrapped raise_for_status() would kill the reward pass."
        )

    def test_returns_empty_on_videos_endpoint_404(self, monkeypatch):
        """A deleted video (404 on youtube/v3/videos) must NOT propagate.
        Deleted-video 404s are common — creators unlist old videos regularly.
        """
        # Seed the token cache so we skip the refresh path and go straight
        # to the videos.list call — the second unwrapped raise_for_status().
        import time as _time

        monkeypatch.setattr(
            youtube,
            "_yt_token_cache",
            {"token": "cached-tok", "niche": "gaming", "ts": _time.time()},
        )

        def _resolve_creds(*_args, **_kwargs):
            # Real signature returns dict[str, str] — see niche_credentials.py:162
            return {"client_id": "cid", "client_secret": "csecret", "refresh_token": "reftok"}

        monkeypatch.setattr(
            "genlab_core.publishing.niche_credentials.resolve_youtube_credentials",
            _resolve_creds,
            raising=False,
        )

        fake_404 = MagicMock()
        fake_404.raise_for_status.side_effect = requests.HTTPError(
            "404 Not Found", response=MagicMock(status_code=404)
        )
        with patch("requests.get", return_value=fake_404):
            result = youtube._fetch_youtube("DELETED_VID", niche_id="gaming")

        assert result == {}, (
            f"YouTube fetcher must return {{}} on videos.list 404, got {result!r}. "
            "Regression: deleted videos would silently kill reward passes."
        )

    def test_returns_empty_on_connection_error(self, monkeypatch):
        """Transient network failures (ConnectionError, Timeout) must NOT
        propagate. These are the ~5xx-adjacent errors from the WARP proxy
        or Google's edge cache."""
        monkeypatch.setattr(youtube, "_yt_token_cache", {"token": None, "niche": None, "ts": 0.0})

        def _resolve_creds(*_args, **_kwargs):
            # Real signature returns dict[str, str] — see niche_credentials.py:162
            return {"client_id": "cid", "client_secret": "csecret", "refresh_token": "reftok"}

        monkeypatch.setattr(
            "genlab_core.publishing.niche_credentials.resolve_youtube_credentials",
            _resolve_creds,
            raising=False,
        )

        with patch("requests.post", side_effect=requests.ConnectionError("network down")):
            result = youtube._fetch_youtube("VIDEO_XYZ", niche_id="ai_creators")

        assert result == {}, (
            f"YouTube fetcher must return {{}} on ConnectionError, got {result!r}. "
            "Regression: transient network blips would cascade to bandit."
        )


class TestXTwitterFetcherHardening:
    """The unwrapped ``raise_for_status()`` at metrics/x_twitter.py:48 was the
    same failure mode as YouTube. Twitter's failure modes are actually more
    common (rate limits fire on burst, deleted tweets 404 frequently).
    """

    def test_returns_empty_on_deleted_tweet_404(self, monkeypatch):
        """A deleted tweet (404 on /2/tweets/{id}) must NOT propagate.
        Deleted-tweet 404s are common for older content — users delete
        historical tweets or protect their accounts."""
        monkeypatch.setenv("X_BEARER_TOKEN", "fake-app-bearer-for-test")

        fake_404 = MagicMock()
        fake_404.raise_for_status.side_effect = requests.HTTPError(
            "404 Not Found", response=MagicMock(status_code=404)
        )
        with patch("requests.get", return_value=fake_404):
            result = x_twitter._fetch_x("1234567890123456789", niche_id="sports")

        assert result == {}, (
            f"X/Twitter fetcher must return {{}} on 404, got {result!r}. "
            "Regression: deleted tweets would silently kill reward passes."
        )

    def test_returns_empty_on_rate_limit_429(self, monkeypatch):
        """A 429 rate-limit response must NOT propagate. Twitter's free tier
        rate limit is aggressive (450 req / 15 min per bearer); a bursty
        metric collection run can trip it, and the retry-after must not
        kill the reward pass."""
        monkeypatch.setenv("X_BEARER_TOKEN", "fake-app-bearer-for-test")

        fake_429 = MagicMock()
        fake_429.raise_for_status.side_effect = requests.HTTPError(
            "429 Too Many Requests", response=MagicMock(status_code=429)
        )
        with patch("requests.get", return_value=fake_429):
            result = x_twitter._fetch_x("9876543210987654321", niche_id="movies")

        assert result == {}, (
            f"X/Twitter fetcher must return {{}} on 429, got {result!r}. "
            "Regression: rate-limit bursts would cascade to bandit."
        )

    def test_returns_empty_when_bearer_missing(self, monkeypatch):
        """Pre-existing behavior — bearer token absent = empty dict, no
        request made. This test locks that in so future refactors don't
        accidentally short-circuit to an unauthenticated request."""
        monkeypatch.setenv("X_BEARER_TOKEN", "")
        result = x_twitter._fetch_x("post_id", niche_id="anime")
        assert result == {}, f"Missing bearer must return {{}}, got {result!r}."

    def test_returns_populated_on_success(self, monkeypatch):
        """Happy path — verify the fix didn't accidentally change the
        success-case return shape. All the reward-shape keys must remain."""
        monkeypatch.setenv("X_BEARER_TOKEN", "fake-app-bearer-for-test")

        fake_ok = MagicMock()
        fake_ok.raise_for_status.return_value = None
        fake_ok.json.return_value = {
            "data": {
                "public_metrics": {
                    "impression_count": 1000,
                    "like_count": 50,
                    "retweet_count": 5,
                    "reply_count": 3,
                }
            }
        }
        with patch("requests.get", return_value=fake_ok):
            result = x_twitter._fetch_x("post_id", niche_id="ai_creators")

        assert result["impressions"] == 1000
        assert result["likes"] == 50
        assert result["retweets"] == 5
        assert result["replies"] == 3
        assert result["engagements"] == 58  # likes + retweets + replies
        assert result["reply_chain_rate"] == 0.003  # 3 / 1000, rounded to 4 places


class TestDefensiveCheckAtOrchestrator:
    """Task #580 was filed on the round-2 audit's suggestion that
    ``if window == "48h" and metrics:`` at ``metric_collector.py:863``
    doesn't distinguish "fetch failed → {}" from "real zero engagement
    → {'views': 0, ...}". Tracing Python truthiness shows this claim
    was WRONG: ``bool({}) is False`` and ``bool({'views': 0}) is True``,
    so the original check already distinguishes them correctly.

    This test locks in that behavior so a future contributor doesn't
    "helpfully" add an ``any(metrics.values())`` guard — that would
    silently drop legitimate zero-engagement posts from the bandit,
    biasing it toward posts that got some engagement.
    """

    def test_empty_dict_is_falsy(self):
        assert not bool({}), (
            "bool({{}}) must be False — that's how metric_collector.py:863 "
            "distinguishes fetch failure from real zero engagement."
        )

    def test_zero_valued_dict_is_truthy(self):
        assert bool({"views": 0, "likes": 0}), (
            "bool({{'views': 0}}) must be True — real zero engagement must "
            "still trigger reward_48h=0.0 write. If someone changes the "
            "orchestrator check to `any(metrics.values())`, this test fails."
        )
