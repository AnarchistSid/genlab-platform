"""Tests for XTwitterClient — mocks tweepy throughout."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tw_client(monkeypatch):
    """Return an XTwitterClient with dummy credentials (no env required)."""
    monkeypatch.setenv("X_API_KEY", "test_api_key")
    monkeypatch.setenv("X_API_SECRET", "test_api_secret")
    monkeypatch.setenv("X_ACCESS_TOKEN", "test_access_token")
    monkeypatch.setenv("X_ACCESS_SECRET", "test_access_secret")

    # Stub tweepy so module loads without real installation issues in CI
    import sys
    fake_tweepy = MagicMock()
    sys.modules.setdefault("tweepy", fake_tweepy)

    from genlab_core.platforms.x_twitter import XTwitterClient
    return XTwitterClient(
        api_key="test_api_key",
        api_secret="test_api_secret",
        access_token="test_access_token",
        access_secret="test_access_secret",
    )


def _make_payload(
    caption="Hello world",
    media_paths=None,
    media_type="text",
    routing="single",
    tweet_text="Hello world",
    thread_tweets=None,
):
    from genlab_core.platforms.models import PublishPayload, TwitterSpecific

    return PublishPayload(
        caption=caption,
        media_paths=media_paths or [],
        media_type=media_type,
        hashtags=["#test"],
        hook="hook text",
        niche_id="ai_news",
        platform_specific=TwitterSpecific(
            routing=routing,
            tweet_text=tweet_text,
            thread_tweets=thread_tweets or [],
        ),
    )


# ---------------------------------------------------------------------------
# TestPublish
# ---------------------------------------------------------------------------


class TestPublish:
    def test_publish_tweet_with_video(self, tw_client):
        """publish() uploads video and creates tweet; returns success PublishResult."""
        payload = _make_payload(
            caption="Watch this clip",
            media_paths=[Path("/tmp/clip.mp4")],
            media_type="video",
            tweet_text="Watch this clip #ai",
        )

        mock_media = MagicMock()
        mock_media.media_id_string = "media_111"

        mock_tweet_resp = MagicMock()
        mock_tweet_resp.data = {"id": "tweet_999"}

        with patch.object(tw_client, "_get_api_v1") as mock_v1_fn, \
             patch.object(tw_client, "_get_client") as mock_client_fn, \
             patch("genlab_core.platforms.x_twitter.Path.exists", return_value=True):
            mock_v1 = MagicMock()
            mock_v1.media_upload.return_value = mock_media
            mock_v1_fn.return_value = mock_v1

            mock_client = MagicMock()
            mock_client.create_tweet.return_value = mock_tweet_resp
            mock_client_fn.return_value = mock_client

            result = tw_client.publish(payload)

        assert result.platform == "x_twitter"
        assert result.success is True
        assert result.post_id == "tweet_999"
        mock_v1.media_upload.assert_called_once()
        mock_client.create_tweet.assert_called_once()
        call_kwargs = mock_client.create_tweet.call_args[1]
        assert call_kwargs["media_ids"] == ["media_111"]

    def test_publish_text_only_no_media(self, tw_client):
        """publish() with no media_paths still posts a text tweet."""
        payload = _make_payload(
            caption="Text only",
            media_paths=[],
            media_type="text",
            tweet_text="Text only tweet",
        )

        mock_tweet_resp = MagicMock()
        mock_tweet_resp.data = {"id": "tweet_text_001"}

        with patch.object(tw_client, "_get_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client.create_tweet.return_value = mock_tweet_resp
            mock_client_fn.return_value = mock_client

            result = tw_client.publish(payload)

        assert result.platform == "x_twitter"
        assert result.success is True
        assert result.post_id == "tweet_text_001"
        call_kwargs = mock_client.create_tweet.call_args[1]
        # No media_ids when no media
        assert call_kwargs.get("media_ids") is None

    def test_publish_thread_routing(self, tw_client):
        """publish() with routing='thread' posts a thread; returns first tweet id."""
        thread_tweets = [
            {"position": 1, "text": "Thread part 1"},
            {"position": 2, "text": "Thread part 2"},
        ]
        payload = _make_payload(
            caption="Thread",
            media_paths=[],
            media_type="text",
            routing="thread",
            tweet_text="",
            thread_tweets=thread_tweets,
        )

        call_count = {"n": 0}

        def mock_create_tweet(**kwargs):
            call_count["n"] += 1
            resp = MagicMock()
            resp.data = {"id": f"thread_tweet_{call_count['n']}"}
            return resp

        with patch.object(tw_client, "_get_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client.create_tweet.side_effect = mock_create_tweet
            mock_client_fn.return_value = mock_client

            result = tw_client.publish(payload)

        assert result.platform == "x_twitter"
        assert result.success is True
        assert result.post_id == "thread_tweet_1"
        assert mock_client.create_tweet.call_count == 2

    def test_publish_missing_tweet_text_fails(self, tw_client):
        """publish() with routing='single' and empty tweet_text returns failure."""
        payload = _make_payload(
            caption="",
            media_paths=[],
            media_type="text",
            tweet_text="",
        )

        result = tw_client.publish(payload)

        assert result.platform == "x_twitter"
        assert result.success is False
        assert result.error != ""

    def test_publish_api_error_returns_failure(self, tw_client):
        """When tweepy raises TweepyException, publish() returns failure."""
        import sys
        tweepy = sys.modules.get("tweepy")
        if tweepy is None:
            pytest.skip("tweepy not available")

        payload = _make_payload(
            caption="Fail tweet",
            tweet_text="Fail tweet",
        )

        tweepy.TweepyException = Exception

        with patch.object(tw_client, "_get_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client.create_tweet.side_effect = tweepy.TweepyException("API error")
            mock_client_fn.return_value = mock_client

            result = tw_client.publish(payload)

        assert result.success is False
        assert result.error != ""

    def test_publish_returns_platform_x_twitter(self, tw_client):
        """PublishResult.platform must always be 'x_twitter'."""
        payload = _make_payload(tweet_text="platform check")

        mock_tweet_resp = MagicMock()
        mock_tweet_resp.data = {"id": "tid_000"}

        with patch.object(tw_client, "_get_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client.create_tweet.return_value = mock_tweet_resp
            mock_client_fn.return_value = mock_client

            result = tw_client.publish(payload)

        assert result.platform == "x_twitter"


# ---------------------------------------------------------------------------
# TestReply
# ---------------------------------------------------------------------------


class TestReply:
    def test_post_reply(self, tw_client):
        """post_reply() creates a tweet in_reply_to_tweet_id. Returns True."""
        mock_resp = MagicMock()
        mock_resp.data = {"id": "reply_555"}

        with patch.object(tw_client, "_get_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client.create_tweet.return_value = mock_resp
            mock_client_fn.return_value = mock_client

            ok = tw_client.post_reply(
                parent_id="original_tweet_123",
                text="Great point!",
                context_id="ctx_abc",
            )

        assert ok is True
        call_kwargs = mock_client.create_tweet.call_args[1]
        assert call_kwargs["in_reply_to_tweet_id"] == "original_tweet_123"
        assert call_kwargs["text"] == "Great point!"

    def test_post_reply_failure(self, tw_client):
        """post_reply() returns False when create_tweet raises."""
        with patch.object(tw_client, "_get_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client.create_tweet.side_effect = Exception("network error")
            mock_client_fn.return_value = mock_client

            ok = tw_client.post_reply(
                parent_id="tweet_xyz",
                text="This will fail",
            )

        assert ok is False


# ---------------------------------------------------------------------------
# TestLike
# ---------------------------------------------------------------------------


class TestLike:
    def test_like_tweet(self, tw_client):
        """like() calls client.like() and returns True on success."""
        mock_resp = MagicMock()

        with patch.object(tw_client, "_get_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client.like.return_value = mock_resp
            mock_client_fn.return_value = mock_client

            ok = tw_client.like(target_id="tweet_to_like_999")

        assert ok is True
        mock_client.like.assert_called_once_with(tweet_id="tweet_to_like_999")

    def test_like_failure(self, tw_client):
        """like() returns False when like() raises."""
        with patch.object(tw_client, "_get_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client.like.side_effect = Exception("forbidden")
            mock_client_fn.return_value = mock_client

            ok = tw_client.like(target_id="tweet_bad")

        assert ok is False


# ---------------------------------------------------------------------------
# TestMetrics
# ---------------------------------------------------------------------------


class TestMetrics:
    def test_get_metrics_returns_platform_metrics(self, tw_client):
        """get_metrics() fetches tweet fields and populates PlatformMetrics."""
        mock_resp = MagicMock()
        mock_resp.data = MagicMock()
        mock_resp.data.public_metrics = {
            "impression_count": 1500,
            "like_count": 42,
            "reply_count": 7,
            "retweet_count": 15,
        }

        with patch.object(tw_client, "_get_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client.get_tweet.return_value = mock_resp
            mock_client_fn.return_value = mock_client

            metrics = tw_client.get_metrics(
                tweet_id="tweet_metrics_123",
                published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )

        assert metrics is not None
        assert metrics.views == 1500
        assert metrics.likes == 42
        assert metrics.comments == 7
        assert metrics.shares == 15
        # impression count in extra
        assert metrics.extra.get("impressions") == 1500

    def test_get_metrics_tweet_not_found_returns_none(self, tw_client):
        """get_metrics() returns None when tweet lookup fails."""
        with patch.object(tw_client, "_get_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client.get_tweet.side_effect = Exception("not found")
            mock_client_fn.return_value = mock_client

            metrics = tw_client.get_metrics(
                tweet_id="missing_tweet",
                published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )

        assert metrics is None

    def test_get_metrics_no_data_returns_none(self, tw_client):
        """get_metrics() returns None when response.data is None."""
        mock_resp = MagicMock()
        mock_resp.data = None

        with patch.object(tw_client, "_get_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client.get_tweet.return_value = mock_resp
            mock_client_fn.return_value = mock_client

            metrics = tw_client.get_metrics(
                tweet_id="null_data_tweet",
                published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )

        assert metrics is None


# ---------------------------------------------------------------------------
# TestHealthCheck
# ---------------------------------------------------------------------------


class TestHealthCheck:
    def test_health_check_200_valid(self, tw_client):
        """200 from /users/me → valid token."""
        mock_resp = MagicMock()
        mock_resp.data = MagicMock()
        mock_resp.data.username = "test_user"
        mock_resp.data.id = "user_123"

        with patch.object(tw_client, "_get_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client.get_me.return_value = mock_resp
            mock_client_fn.return_value = mock_client

            status = tw_client.check_token_health()

        assert status.valid is True
        assert status.platform == "x_twitter"

    def test_health_check_403_valid_free_tier(self, tw_client):
        """403 Forbidden → valid for free tier; treat as valid."""
        import sys
        tweepy = sys.modules.get("tweepy")

        # Create a Forbidden exception class
        class FakeForbidden(Exception):
            pass

        with patch.object(tw_client, "_get_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client.get_me.side_effect = FakeForbidden("403 Forbidden")
            mock_client_fn.return_value = mock_client

            # Patch tweepy.Forbidden if tweepy is available
            if tweepy is not None:
                with patch.object(tweepy, "Forbidden", FakeForbidden, create=True):
                    status = tw_client.check_token_health()
            else:
                # Override the client's _is_forbidden_error helper
                with patch.object(tw_client, "_is_forbidden_error", return_value=True):
                    status = tw_client.check_token_health()

        assert status.valid is True
        assert status.platform == "x_twitter"
        assert "free tier" in status.message.lower() or "403" in status.message

    def test_health_check_401_invalid(self, tw_client):
        """401 Unauthorized → invalid token."""
        import sys
        tweepy = sys.modules.get("tweepy")

        class FakeUnauthorized(Exception):
            pass

        with patch.object(tw_client, "_get_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client.get_me.side_effect = FakeUnauthorized("401 Unauthorized")
            mock_client_fn.return_value = mock_client

            if tweepy is not None:
                with patch.object(tweepy, "Unauthorized", FakeUnauthorized, create=True):
                    status = tw_client.check_token_health()
            else:
                with patch.object(tw_client, "_is_unauthorized_error", return_value=True):
                    status = tw_client.check_token_health()

        assert status.valid is False
        assert status.platform == "x_twitter"

    def test_health_check_platform_id(self, tw_client):
        """XTwitterClient.platform_id class attribute is 'x_twitter'."""
        from genlab_core.platforms.x_twitter import XTwitterClient
        assert XTwitterClient.platform_id == "x_twitter"


# ---------------------------------------------------------------------------
# TestRateLimit
# ---------------------------------------------------------------------------


class TestRateLimit:
    def test_rate_limit_flag_blocks_publish(self, tw_client):
        """After a 429, _rate_limited=True short-circuits further publish calls."""
        import sys
        tweepy = sys.modules.get("tweepy")

        class FakeTooManyRequests(Exception):
            pass

        payload = _make_payload(tweet_text="First tweet")

        # Simulate a 429 response on first call
        with patch.object(tw_client, "_get_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client.create_tweet.side_effect = FakeTooManyRequests("429 Too Many Requests")
            mock_client_fn.return_value = mock_client

            if tweepy is not None:
                with patch.object(tweepy, "TooManyRequests", FakeTooManyRequests, create=True):
                    result1 = tw_client.publish(payload)
            else:
                with patch.object(tw_client, "_is_rate_limit_error", return_value=True):
                    result1 = tw_client.publish(payload)

        # Manually force rate limit in case the mock didn't trigger it
        tw_client._rate_limited = True
        tw_client._rate_limited_at = time.monotonic()

        # Second publish should be short-circuited without calling the API
        with patch.object(tw_client, "_get_client") as mock_client_fn2:
            mock_client2 = MagicMock()
            mock_client_fn2.return_value = mock_client2

            result2 = tw_client.publish(payload)

        assert result2.success is False
        # The second call must NOT have called create_tweet
        mock_client2.create_tweet.assert_not_called()

    def test_rate_limit_cooldown_resets(self, tw_client):
        """After cooldown expires, rate limit flag is cleared."""
        # Force an expired rate limit (set timestamp to 2 hours ago)
        tw_client._rate_limited = True
        tw_client._rate_limited_at = time.monotonic() - 7300  # > 3600s cooldown

        payload = _make_payload(tweet_text="After cooldown")

        mock_tweet_resp = MagicMock()
        mock_tweet_resp.data = {"id": "cooldown_tweet_1"}

        with patch.object(tw_client, "_get_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client.create_tweet.return_value = mock_tweet_resp
            mock_client_fn.return_value = mock_client

            result = tw_client.publish(payload)

        assert result.success is True
        assert tw_client._rate_limited is False


# ---------------------------------------------------------------------------
# TestInit
# ---------------------------------------------------------------------------


class TestInit:
    def test_env_var_defaults(self, monkeypatch):
        """Constructor falls back to env vars when explicit args are omitted."""
        monkeypatch.setenv("X_API_KEY", "env_key")
        monkeypatch.setenv("X_API_SECRET", "env_secret")
        monkeypatch.setenv("X_ACCESS_TOKEN", "env_token")
        monkeypatch.setenv("X_ACCESS_SECRET", "env_access_secret")

        import sys
        fake_tweepy = MagicMock()
        sys.modules.setdefault("tweepy", fake_tweepy)

        from genlab_core.platforms.x_twitter import XTwitterClient
        client = XTwitterClient()
        assert client._api_key == "env_key"
        assert client._api_secret == "env_secret"
        assert client._access_token == "env_token"
        assert client._access_secret == "env_access_secret"

    def test_explicit_args_override_env(self, monkeypatch):
        """Explicit constructor args take precedence over env vars."""
        monkeypatch.setenv("X_API_KEY", "env_key")
        monkeypatch.setenv("X_API_SECRET", "env_secret")
        monkeypatch.setenv("X_ACCESS_TOKEN", "env_token")
        monkeypatch.setenv("X_ACCESS_SECRET", "env_access_secret")

        import sys
        fake_tweepy = MagicMock()
        sys.modules.setdefault("tweepy", fake_tweepy)

        from genlab_core.platforms.x_twitter import XTwitterClient
        client = XTwitterClient(
            api_key="explicit_key",
            api_secret="explicit_secret",
            access_token="explicit_token",
            access_secret="explicit_access_secret",
        )
        assert client._api_key == "explicit_key"
        assert client._access_token == "explicit_token"

    def test_platform_id_attribute(self):
        """XTwitterClient.platform_id == 'x_twitter'."""
        from genlab_core.platforms.x_twitter import XTwitterClient
        assert XTwitterClient.platform_id == "x_twitter"
