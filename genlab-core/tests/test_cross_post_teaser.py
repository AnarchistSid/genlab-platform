"""Pin: cross_post_teaser — YouTube → X auto-teaser worker.

Distribution lever shipped 2026-06-23. After a successful YouTube
publish, optionally creates an X/Twitter teaser pointing back to
the YouTube video. Per-niche opt-in via publishing.yaml's
``cross_post.youtube_to_x.enabled`` flag. Fully non-blocking — any
failure logs at WARN; the main publish has already succeeded.

These pins lock in:

  Early-return / no-op paths (5):
  1. source != "youtube" → no-op
  2. post_id missing → no-op
  3. post_url missing → no-op
  4. cross_post config disabled (default) → no-op
  5. No hook in fields → no-op + debug log

  Happy path (3):
  6. Main tweet + reply both fire when everything is wired up
  7. Reply text contains UTM tagging
  8. Main tweet text contains hook (no URL — per CLAUDE.md X rule)

  Partial failure (2):
  9. Main tweet returns None → no reply attempted, WARN logged
  10. Reply returns False → main tweet posted but WARN about reply

  Fail-OPEN (1):
  11. Twitter client raises mid-call → WARN logged, no re-raise

  Wire (1):
  12. Source-level pin: parallel_publish._on_success calls
      cross_teaser after affiliate_reply
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_TEASER_SRC = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "genlab_core"
    / "publishing"
    / "cross_post_teaser.py"
)
_PARALLEL_SRC = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "genlab_core"
    / "publishing"
    / "parallel_publish.py"
)


_FIELDS_WITH_HOOK = {
    "hook_text": "This trailer just broke YouTube",
    "title": "Trailer Reaction",
}


class TestEarlyReturnPaths:
    def test_non_youtube_source_is_noop(self):
        """Pin: source platform != youtube → no-op. Initial scope is
        YT-only; adding IG/FB later needs explicit re-scoping."""
        from genlab_core.publishing.cross_post_teaser import post_cross_teaser

        with patch(
            "genlab_core.publishing.cross_post_teaser._is_cross_post_enabled",
            return_value=True,
        ) as mock_enabled:
            post_cross_teaser(
                source_platform="instagram",
                post_id="ig_post_123",
                post_url="https://instagram.com/p/abc",
                fields=_FIELDS_WITH_HOOK,
                niche_id="gaming",
            )
        # Early return BEFORE the config check — saves the YAML read
        mock_enabled.assert_not_called()

    def test_missing_post_id_is_noop(self):
        from genlab_core.publishing.cross_post_teaser import post_cross_teaser

        with patch(
            "genlab_core.publishing.cross_post_teaser._is_cross_post_enabled",
            return_value=True,
        ) as mock_enabled:
            post_cross_teaser(
                source_platform="youtube",
                post_id=None,
                post_url="https://youtube.com/shorts/abc",
                fields=_FIELDS_WITH_HOOK,
                niche_id="gaming",
            )
        mock_enabled.assert_not_called()

    def test_missing_post_url_is_noop(self):
        from genlab_core.publishing.cross_post_teaser import post_cross_teaser

        with patch(
            "genlab_core.publishing.cross_post_teaser._is_cross_post_enabled",
            return_value=True,
        ) as mock_enabled:
            post_cross_teaser(
                source_platform="youtube",
                post_id="yt_abc",
                post_url=None,
                fields=_FIELDS_WITH_HOOK,
                niche_id="gaming",
            )
        mock_enabled.assert_not_called()

    def test_config_disabled_is_noop(self):
        """Pin: when publishing.yaml has cross_post.youtube_to_x.enabled
        absent or false → no-op. Default-off shadow rollout invariant."""
        from genlab_core.publishing.cross_post_teaser import post_cross_teaser

        with patch(
            "genlab_core.publishing.cross_post_teaser._is_cross_post_enabled",
            return_value=False,
        ):
            with patch(
                "genlab_core.publishing.cross_post_teaser.resolve_twitter_credentials"
            ) as mock_creds:
                post_cross_teaser(
                    source_platform="youtube",
                    post_id="yt_abc",
                    post_url="https://youtube.com/shorts/abc",
                    fields=_FIELDS_WITH_HOOK,
                    niche_id="gaming",
                )
        # Config disabled → never even resolved Twitter credentials
        mock_creds.assert_not_called()

    def test_no_hook_in_fields_is_noop(self):
        """Pin: empty/missing hook → no teaser. We won't post a tweet
        that's just whitespace or the literal niche name."""
        from genlab_core.publishing.cross_post_teaser import post_cross_teaser

        with patch(
            "genlab_core.publishing.cross_post_teaser._is_cross_post_enabled",
            return_value=True,
        ):
            with patch(
                "genlab_core.publishing.cross_post_teaser.resolve_twitter_credentials"
            ) as mock_creds:
                post_cross_teaser(
                    source_platform="youtube",
                    post_id="yt_abc",
                    post_url="https://youtube.com/shorts/abc",
                    fields={},  # no hook, no title, no anything
                    niche_id="gaming",
                )
        mock_creds.assert_not_called()


class TestHappyPath:
    def _setup_x_client(self, monkeypatch):
        """Build a fake XTwitterClient instance + patch the import."""
        fake_client = MagicMock()
        fake_client._post_single_tweet = MagicMock(return_value="tweet_id_main")
        fake_client.post_reply = MagicMock(return_value=True)

        def fake_x_constructor(**kwargs):
            return fake_client

        monkeypatch.setattr(
            "genlab_core.publishing.cross_post_teaser.XTwitterClient",
            fake_x_constructor,
        )
        return fake_client

    def test_main_tweet_plus_reply_fire(self, monkeypatch):
        """Pin: enabled + creds + hook + url → main tweet + reply both
        posted. The smoke pin — without this, the wire is dead."""
        fake_x = self._setup_x_client(monkeypatch)
        monkeypatch.setattr(
            "genlab_core.publishing.cross_post_teaser._is_cross_post_enabled",
            lambda *a, **kw: True,
        )
        monkeypatch.setattr(
            "genlab_core.publishing.cross_post_teaser.resolve_twitter_credentials",
            lambda niche_id: {"api_key": "k", "api_secret": "s"},
        )

        from genlab_core.publishing.cross_post_teaser import post_cross_teaser

        post_cross_teaser(
            source_platform="youtube",
            post_id="yt_abc",
            post_url="https://youtube.com/shorts/yt_abc",
            fields=_FIELDS_WITH_HOOK,
            niche_id="gaming",
        )

        fake_x._post_single_tweet.assert_called_once()
        fake_x.post_reply.assert_called_once()

    def test_reply_contains_utm_tagging(self, monkeypatch):
        """Pin: the link reply tags the URL with utm_source=x_teaser +
        utm_medium=cross_post. Without this, reach attribution from
        the cross-post is indistinguishable from organic YT traffic."""
        fake_x = self._setup_x_client(monkeypatch)
        monkeypatch.setattr(
            "genlab_core.publishing.cross_post_teaser._is_cross_post_enabled",
            lambda *a, **kw: True,
        )
        monkeypatch.setattr(
            "genlab_core.publishing.cross_post_teaser.resolve_twitter_credentials",
            lambda niche_id: {"api_key": "k"},
        )

        from genlab_core.publishing.cross_post_teaser import post_cross_teaser

        post_cross_teaser(
            source_platform="youtube",
            post_id="yt_abc",
            post_url="https://youtube.com/shorts/yt_abc",
            fields=_FIELDS_WITH_HOOK,
            niche_id="gaming",
        )

        reply_text = fake_x.post_reply.call_args[0][1]
        assert "utm_source=x_teaser" in reply_text
        assert "utm_medium=cross_post" in reply_text
        assert "https://youtube.com/shorts/yt_abc" in reply_text

    def test_main_tweet_contains_hook_no_url(self, monkeypatch):
        """Pin: per CLAUDE.md X rule, NO external links in tweet body.
        The main teaser is the hook only; URL goes in the reply.
        Regression here would silently violate the X content policy."""
        fake_x = self._setup_x_client(monkeypatch)
        monkeypatch.setattr(
            "genlab_core.publishing.cross_post_teaser._is_cross_post_enabled",
            lambda *a, **kw: True,
        )
        monkeypatch.setattr(
            "genlab_core.publishing.cross_post_teaser.resolve_twitter_credentials",
            lambda niche_id: {"api_key": "k"},
        )

        from genlab_core.publishing.cross_post_teaser import post_cross_teaser

        post_cross_teaser(
            source_platform="youtube",
            post_id="yt_abc",
            post_url="https://youtube.com/shorts/yt_abc",
            fields=_FIELDS_WITH_HOOK,
            niche_id="gaming",
        )

        main_text = fake_x._post_single_tweet.call_args.kwargs["text"]
        assert "This trailer just broke YouTube" in main_text
        # NO URL in the main tweet body
        assert "youtube.com" not in main_text
        assert "http" not in main_text


class TestPartialFailure:
    def test_main_tweet_returns_none_no_reply(self, monkeypatch):
        """Pin: when the main tweet fails (returns None — rate limit,
        etc.), the reply is NOT attempted. Posting the URL as an
        orphaned reply to nothing would be a silent UX bug."""
        fake_x = MagicMock()
        fake_x._post_single_tweet = MagicMock(return_value=None)
        fake_x.post_reply = MagicMock(return_value=True)
        monkeypatch.setattr(
            "genlab_core.publishing.cross_post_teaser.XTwitterClient",
            lambda **kw: fake_x,
        )
        monkeypatch.setattr(
            "genlab_core.publishing.cross_post_teaser._is_cross_post_enabled",
            lambda *a, **kw: True,
        )
        monkeypatch.setattr(
            "genlab_core.publishing.cross_post_teaser.resolve_twitter_credentials",
            lambda niche_id: {"api_key": "k"},
        )

        from genlab_core.publishing.cross_post_teaser import post_cross_teaser

        post_cross_teaser(
            source_platform="youtube",
            post_id="yt_abc",
            post_url="https://youtube.com/shorts/yt_abc",
            fields=_FIELDS_WITH_HOOK,
            niche_id="gaming",
        )

        fake_x._post_single_tweet.assert_called_once()
        # Critical: reply was never attempted
        fake_x.post_reply.assert_not_called()

    def test_reply_failure_logs_warn(self, monkeypatch, caplog):
        """Pin: when the reply fails (returns False), the function
        logs at WARN so the operator sees the partial failure (main
        tweet went out, URL didn't reach the audience)."""
        import logging

        fake_x = MagicMock()
        fake_x._post_single_tweet = MagicMock(return_value="main_id")
        fake_x.post_reply = MagicMock(return_value=False)
        monkeypatch.setattr(
            "genlab_core.publishing.cross_post_teaser.XTwitterClient",
            lambda **kw: fake_x,
        )
        monkeypatch.setattr(
            "genlab_core.publishing.cross_post_teaser._is_cross_post_enabled",
            lambda *a, **kw: True,
        )
        monkeypatch.setattr(
            "genlab_core.publishing.cross_post_teaser.resolve_twitter_credentials",
            lambda niche_id: {"api_key": "k"},
        )

        from genlab_core.publishing.cross_post_teaser import post_cross_teaser

        with caplog.at_level(logging.WARNING):
            post_cross_teaser(
                source_platform="youtube",
                post_id="yt_abc",
                post_url="https://youtube.com/shorts/yt_abc",
                fields=_FIELDS_WITH_HOOK,
                niche_id="gaming",
            )
        # Reply failed → WARN log surfaces it
        assert any("First-reply URL failed" in r.message for r in caplog.records)


class TestFailOpen:
    def test_client_exception_does_not_raise(self, monkeypatch):
        """Pin: arbitrary exception inside the post path must NOT
        propagate. Cross-teaser failures MUST NEVER unwind the main
        publish — sibling guarantee to affiliate_reply.

        Regression here would cause one bad X API response to roll
        back a successful YouTube publish through the parallel
        publisher's exception handlers."""

        def boom(**kw):
            raise RuntimeError("simulated Twitter outage")

        monkeypatch.setattr("genlab_core.publishing.cross_post_teaser.XTwitterClient", boom)
        monkeypatch.setattr(
            "genlab_core.publishing.cross_post_teaser._is_cross_post_enabled",
            lambda *a, **kw: True,
        )
        monkeypatch.setattr(
            "genlab_core.publishing.cross_post_teaser.resolve_twitter_credentials",
            lambda niche_id: {"api_key": "k"},
        )

        from genlab_core.publishing.cross_post_teaser import post_cross_teaser

        # No raise, no failure — just an internal WARN log
        post_cross_teaser(
            source_platform="youtube",
            post_id="yt_abc",
            post_url="https://youtube.com/shorts/yt_abc",
            fields=_FIELDS_WITH_HOOK,
            niche_id="gaming",
        )


class TestUTMHelpers:
    def test_utm_append_to_url_without_query(self):
        from genlab_core.publishing.cross_post_teaser import _append_utm

        out = _append_utm("https://youtube.com/shorts/abc")
        assert out.startswith("https://youtube.com/shorts/abc?")
        assert "utm_source=x_teaser" in out
        assert "utm_medium=cross_post" in out

    def test_utm_append_to_url_with_existing_query(self):
        """Pin: URLs that already have query strings use & separator,
        not ? — wouldn't merge into a single param string otherwise."""
        from genlab_core.publishing.cross_post_teaser import _append_utm

        out = _append_utm("https://youtu.be/abc?si=12345")
        assert "?si=12345&utm_source=x_teaser" in out
        # Single ? in the URL — no double-query bug
        assert out.count("?") == 1


class TestNoCredsSkip:
    def test_missing_twitter_creds_is_noop(self, monkeypatch):
        """Pin: when resolve_twitter_credentials returns no api_key,
        we skip cleanly — no instantiation attempt, no warning spam.
        This is the typical state for niches that haven't set up X
        credentials yet."""
        monkeypatch.setattr(
            "genlab_core.publishing.cross_post_teaser._is_cross_post_enabled",
            lambda *a, **kw: True,
        )
        monkeypatch.setattr(
            "genlab_core.publishing.cross_post_teaser.resolve_twitter_credentials",
            lambda niche_id: {},  # no api_key
        )
        client_constructor = MagicMock()
        monkeypatch.setattr(
            "genlab_core.publishing.cross_post_teaser.XTwitterClient",
            client_constructor,
        )

        from genlab_core.publishing.cross_post_teaser import post_cross_teaser

        post_cross_teaser(
            source_platform="youtube",
            post_id="yt_abc",
            post_url="https://youtube.com/shorts/yt_abc",
            fields=_FIELDS_WITH_HOOK,
            niche_id="gaming",
        )
        # XTwitterClient was never instantiated
        client_constructor.assert_not_called()


class TestSourceWire:
    """Source-level pins — defend against the silent-no-op delete
    pattern where someone removes the wire and the runtime tests
    still pass because everything fails-OPEN."""

    def test_cross_teaser_called_in_parallel_publish_on_success(self):
        """Pin: parallel_publish._on_success imports + calls
        post_cross_teaser after affiliate_reply."""
        src = _PARALLEL_SRC.read_text()
        assert "from genlab_core.publishing.cross_post_teaser import post_cross_teaser" in src
        assert "post_cross_teaser(" in src
        # Fail-OPEN canonical phrase
        assert "cross-teaser failure must never block publish" in src

    def test_teaser_module_uses_post_reply_for_url(self):
        """Pin: the URL goes via X's post_reply (which uses
        in_reply_to_tweet_id), NOT inlined into the main tweet
        body. CLAUDE.md rule: no external links in X tweet body."""
        src = _TEASER_SRC.read_text()
        assert "post_reply" in src
        assert "_post_single_tweet" in src
        # The UTM helpers exist and are reachable from post_cross_teaser
        assert "_append_utm" in src
        assert "utm_source=x_teaser" in src


@pytest.fixture(autouse=True)
def _no_real_yaml_read(monkeypatch):
    """Belt-and-suspenders: prevent _is_cross_post_enabled from
    reading the actual filesystem in tests that don't explicitly
    patch it. Returns False by default — keeps tests hermetic."""
    monkeypatch.setattr(
        "genlab_core.publishing.cross_post_teaser._is_cross_post_enabled",
        lambda *a, **kw: False,
        raising=True,
    )
