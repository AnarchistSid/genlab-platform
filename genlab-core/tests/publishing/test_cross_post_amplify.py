"""Pin Phase 3.E cross-platform amplification:

  * YT → Threads only fires when source_platform == 'youtube'
  * YT → Threads only fires when config flag on
  * YT → Threads skips gracefully when no hook
  * FB self-comment only fires when reach >= min_reach
  * FB self-comment skips wrong source
  * FB self-comment skips missing yt_url
  * UTM tags applied to URLs
  * Config-read fail-opens to False (safe default)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from genlab_core.publishing import cross_post_amplify as cpa


class TestUTM:
    def test_append_no_existing_query(self):
        out = cpa._append_utm("https://youtube.com/shorts/abc", "test")
        assert out == "https://youtube.com/shorts/abc?utm_source=test&utm_medium=cross_post"

    def test_append_with_existing_query(self):
        out = cpa._append_utm("https://youtube.com/watch?v=abc", "test")
        assert "?" in out
        assert "&utm_source=test" in out

    def test_empty_url_returns_empty(self):
        assert cpa._append_utm("", "test") == ""


class TestThreadsAmplifyBuilder:
    def test_uses_hook_text_first(self):
        out = cpa._build_threads_amplify_text(
            {"hook_text": "The hook", "title": "The title"},
            "https://youtube.com/shorts/abc",
        )
        assert out is not None
        assert "The hook" in out
        assert "The title" not in out
        assert "youtube.com/shorts/abc" in out
        assert "utm_source=threads_amplify" in out

    def test_falls_back_to_title_when_no_hook(self):
        out = cpa._build_threads_amplify_text(
            {"title": "The title"},
            "https://youtube.com/shorts/abc",
        )
        assert out is not None
        assert "The title" in out

    def test_missing_hook_and_title_returns_none(self):
        assert cpa._build_threads_amplify_text({}, "https://x.com/") is None

    def test_missing_url_returns_none(self):
        assert cpa._build_threads_amplify_text({"hook_text": "hi"}, "") is None

    def test_caps_at_500_chars(self):
        long_hook = "A" * 600
        out = cpa._build_threads_amplify_text(
            {"hook_text": long_hook}, "https://youtube.com/shorts/x",
        )
        assert out is not None
        assert len(out) <= cpa._MAX_THREADS_CHARS


class TestYouTubeToThreadsGate:
    def _fake_threads(self, publish_result_ok=True):
        client = MagicMock()
        result = MagicMock()
        result.success = publish_result_ok
        result.post_id = "threads_123"
        result.error = None if publish_result_ok else "boom"
        client._publish_text.return_value = result
        return client

    def test_non_youtube_source_skipped(self):
        """Only YT source triggers this route."""
        with patch.object(cpa, "_route_enabled", return_value=True):
            ok = cpa.post_youtube_to_threads_amplify(
                "instagram", "https://youtube.com/x",
                {"hook_text": "hi"}, "gaming",
                _threads_client_factory=lambda n: self._fake_threads(),
            )
        assert ok is False

    def test_route_disabled_skipped(self):
        with patch.object(cpa, "_route_enabled", return_value=False):
            ok = cpa.post_youtube_to_threads_amplify(
                "youtube", "https://youtube.com/x",
                {"hook_text": "hi"}, "gaming",
                _threads_client_factory=lambda n: self._fake_threads(),
            )
        assert ok is False

    def test_route_enabled_publish_succeeds(self):
        with patch.object(cpa, "_route_enabled", return_value=True):
            fake = self._fake_threads(publish_result_ok=True)
            ok = cpa.post_youtube_to_threads_amplify(
                "youtube", "https://youtube.com/x",
                {"hook_text": "hi"}, "gaming",
                _threads_client_factory=lambda n: fake,
            )
        assert ok is True
        # Verify _publish_text was called with a body containing the hook
        call = fake._publish_text.call_args
        assert "hi" in call.kwargs["caption"]

    def test_route_enabled_no_hook_skipped(self):
        """Even with flag on + real client, no hook = skip (returns
        False, no publish call)."""
        with patch.object(cpa, "_route_enabled", return_value=True):
            fake = self._fake_threads()
            ok = cpa.post_youtube_to_threads_amplify(
                "youtube", "https://youtube.com/x",
                {},  # no hook
                "gaming",
                _threads_client_factory=lambda n: fake,
            )
        assert ok is False
        fake._publish_text.assert_not_called()

    def test_publish_failure_returns_false(self):
        with patch.object(cpa, "_route_enabled", return_value=True):
            fake = self._fake_threads(publish_result_ok=False)
            ok = cpa.post_youtube_to_threads_amplify(
                "youtube", "https://youtube.com/x",
                {"hook_text": "hi"}, "gaming",
                _threads_client_factory=lambda n: fake,
            )
        assert ok is False


class TestFacebookSelfCommentGate:
    def _fake_fb(self, ok=True):
        client = MagicMock()
        client.post_reply.return_value = ok
        return client

    def test_non_facebook_source_skipped(self):
        with patch.object(cpa, "_route_enabled", return_value=True):
            ok = cpa.post_facebook_self_comment(
                "instagram", "fb_post_1", 5000, "https://youtube.com/x", "gaming",
                _fb_client_factory=lambda n: self._fake_fb(),
            )
        assert ok is False

    def test_route_disabled_skipped(self):
        with patch.object(cpa, "_route_enabled", return_value=False):
            ok = cpa.post_facebook_self_comment(
                "facebook", "fb_post_1", 5000, "https://youtube.com/x", "gaming",
                _fb_client_factory=lambda n: self._fake_fb(),
            )
        assert ok is False

    def test_reach_below_threshold_skipped(self):
        """Roadmap invariant — only self-comment on high-reach
        posts. Comment-spam feel on low-reach posts is worse than
        skipping."""
        with patch.object(cpa, "_route_enabled", return_value=True), \
             patch.object(cpa, "_fb_min_reach_threshold", return_value=1000):
            fake = self._fake_fb()
            ok = cpa.post_facebook_self_comment(
                "facebook", "fb_post_1", 500,  # below 1000
                "https://youtube.com/x", "gaming",
                _fb_client_factory=lambda n: fake,
            )
        assert ok is False
        fake.post_reply.assert_not_called()

    def test_reach_at_threshold_fires(self):
        with patch.object(cpa, "_route_enabled", return_value=True), \
             patch.object(cpa, "_fb_min_reach_threshold", return_value=1000):
            fake = self._fake_fb(ok=True)
            ok = cpa.post_facebook_self_comment(
                "facebook", "fb_post_1", 1000,  # at threshold
                "https://youtube.com/x", "gaming",
                _fb_client_factory=lambda n: fake,
            )
        assert ok is True
        fake.post_reply.assert_called_once()

    def test_missing_yt_url_skipped(self):
        with patch.object(cpa, "_route_enabled", return_value=True):
            ok = cpa.post_facebook_self_comment(
                "facebook", "fb_post_1", 5000, "", "gaming",
                _fb_client_factory=lambda n: self._fake_fb(),
            )
        assert ok is False

    def test_utm_appears_in_comment(self):
        with patch.object(cpa, "_route_enabled", return_value=True), \
             patch.object(cpa, "_fb_min_reach_threshold", return_value=1000):
            fake = self._fake_fb(ok=True)
            cpa.post_facebook_self_comment(
                "facebook", "fb_post_1", 5000,
                "https://youtube.com/shorts/xyz", "gaming",
                _fb_client_factory=lambda n: fake,
            )
        comment = fake.post_reply.call_args.args[1]
        assert "utm_source=fb_self_comment" in comment
        assert "youtube.com/shorts/xyz" in comment


class TestConfigReadFailOpen:
    def test_missing_config_returns_false(self, monkeypatch):
        """Any config-read exception → route disabled = safe default."""
        with patch.object(cpa, "_load_publishing_yaml", return_value={}):
            assert cpa._route_enabled("gaming", "youtube_to_threads") is False

    def test_malformed_cross_post_key_returns_false(self):
        with patch.object(
            cpa, "_load_publishing_yaml",
            return_value={"cross_post": "not a dict"},
        ):
            assert cpa._route_enabled("gaming", "youtube_to_threads") is False

    def test_malformed_route_key_returns_false(self):
        with patch.object(
            cpa, "_load_publishing_yaml",
            return_value={"cross_post": {"youtube_to_threads": "yes"}},
        ):
            assert cpa._route_enabled("gaming", "youtube_to_threads") is False

    def test_enabled_true_returns_true(self):
        with patch.object(
            cpa, "_load_publishing_yaml",
            return_value={
                "cross_post": {"youtube_to_threads": {"enabled": True}}
            },
        ):
            assert cpa._route_enabled("gaming", "youtube_to_threads") is True

    def test_min_reach_default_1000(self):
        with patch.object(cpa, "_load_publishing_yaml", return_value={}):
            assert cpa._fb_min_reach_threshold("gaming") == 1000

    def test_min_reach_configurable(self):
        with patch.object(
            cpa, "_load_publishing_yaml",
            return_value={
                "cross_post": {"facebook_self_comment": {"min_reach": 5000}}
            },
        ):
            assert cpa._fb_min_reach_threshold("gaming") == 5000
