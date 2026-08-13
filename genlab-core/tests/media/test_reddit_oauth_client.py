"""Pin `reddit_oauth_client` stub behavior.

Stub is intentionally minimal — it should:
  * Return False from is_oauth_enabled() when flag off OR any env var missing
  * Return None from get_reddit_client() in the same conditions
  * Return empty list from fetch_subreddit_top when client is None
  * Never raise even when praw is missing (falls back to warn + None)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


ALL_ENV_VARS = (
    "GENLAB_REDDIT_OAUTH_ENABLED",
    "REDDIT_CLIENT_ID",
    "REDDIT_CLIENT_SECRET",
    "REDDIT_USER_AGENT",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Every test starts with these vars unset — subsequent tests set what they need."""
    for v in ALL_ENV_VARS:
        monkeypatch.delenv(v, raising=False)


class TestIsOAuthEnabled:
    def test_flag_off_default(self):
        from genlab_core.media.reddit_oauth_client import is_oauth_enabled
        assert not is_oauth_enabled()

    def test_flag_on_but_no_creds(self, monkeypatch):
        monkeypatch.setenv("GENLAB_REDDIT_OAUTH_ENABLED", "1")
        from genlab_core.media.reddit_oauth_client import is_oauth_enabled
        assert not is_oauth_enabled()

    def test_flag_on_partial_creds(self, monkeypatch):
        monkeypatch.setenv("GENLAB_REDDIT_OAUTH_ENABLED", "1")
        monkeypatch.setenv("REDDIT_CLIENT_ID", "abc")
        # Missing SECRET + USER_AGENT
        from genlab_core.media.reddit_oauth_client import is_oauth_enabled
        assert not is_oauth_enabled()

    def test_all_present(self, monkeypatch):
        monkeypatch.setenv("GENLAB_REDDIT_OAUTH_ENABLED", "true")
        monkeypatch.setenv("REDDIT_CLIENT_ID", "abc")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "xyz")
        monkeypatch.setenv("REDDIT_USER_AGENT", "GenLab/1.0 by /u/test")
        from genlab_core.media.reddit_oauth_client import is_oauth_enabled
        assert is_oauth_enabled()

    def test_empty_string_creds_disable(self, monkeypatch):
        monkeypatch.setenv("GENLAB_REDDIT_OAUTH_ENABLED", "1")
        monkeypatch.setenv("REDDIT_CLIENT_ID", "abc")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "")
        monkeypatch.setenv("REDDIT_USER_AGENT", "ua")
        from genlab_core.media.reddit_oauth_client import is_oauth_enabled
        assert not is_oauth_enabled()


class TestGetRedditClient:
    def test_disabled_returns_none(self):
        from genlab_core.media.reddit_oauth_client import get_reddit_client
        assert get_reddit_client() is None

    def test_enabled_but_praw_missing_returns_none(self, monkeypatch):
        monkeypatch.setenv("GENLAB_REDDIT_OAUTH_ENABLED", "1")
        monkeypatch.setenv("REDDIT_CLIENT_ID", "abc")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "xyz")
        monkeypatch.setenv("REDDIT_USER_AGENT", "ua")

        # Simulate praw not installed
        import builtins
        real_import = builtins.__import__

        def blocked_import(name, *args, **kwargs):
            if name == "praw":
                raise ImportError("no praw for test")
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=blocked_import):
            from genlab_core.media.reddit_oauth_client import get_reddit_client
            assert get_reddit_client() is None

    def test_praw_init_failure_returns_none(self, monkeypatch):
        monkeypatch.setenv("GENLAB_REDDIT_OAUTH_ENABLED", "1")
        monkeypatch.setenv("REDDIT_CLIENT_ID", "abc")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "xyz")
        monkeypatch.setenv("REDDIT_USER_AGENT", "ua")

        # Stand up a fake praw module that raises on Reddit()
        fake_praw = MagicMock()
        fake_praw.Reddit.side_effect = RuntimeError("bad creds")
        with patch.dict("sys.modules", {"praw": fake_praw}):
            from genlab_core.media.reddit_oauth_client import get_reddit_client
            assert get_reddit_client() is None


class TestFetchSubredditTop:
    def test_no_client_returns_empty(self):
        from genlab_core.media.reddit_oauth_client import fetch_subreddit_top
        assert fetch_subreddit_top("aivideo") == []
