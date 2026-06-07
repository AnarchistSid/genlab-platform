"""Tests for :mod:`genlab_core.publishing.affiliate_reply`.

Lives next to its module home (paralleling the PR 5/N extraction in
the publish_all_platforms decomposition).

Strategy
--------
``post_affiliate_reply`` makes platform-specific outbound API calls.
We patch the per-platform clients + the credential resolvers + the
``requests.post`` used by the IG branch so the tests stay hermetic
and assert on the call shape. The "never propagate" exception guard
gets dedicated coverage because losing the main post over a failed
affiliate comment would be a real outage.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from genlab_core.publishing.affiliate_reply import post_affiliate_reply

# ---------------------------------------------------------------------------
# Early-return short-circuits — each must NOT touch any client / network.
# ---------------------------------------------------------------------------


class TestEarlyReturns:
    def test_missing_post_id_returns_without_touching_clients(self) -> None:
        with patch("genlab_core.publishing.affiliate_reply.requests") as mock_req:
            post_affiliate_reply(
                "instagram", None, {"affiliate_url": "u", "affiliate_product": "p"}, "gaming"
            )
        mock_req.post.assert_not_called()

    def test_missing_affiliate_url_returns(self) -> None:
        with patch("genlab_core.publishing.affiliate_reply.requests") as mock_req:
            post_affiliate_reply(
                "instagram", "id123", {"affiliate_url": "", "affiliate_product": "p"}, "gaming"
            )
        mock_req.post.assert_not_called()

    def test_missing_affiliate_product_returns(self) -> None:
        with patch("genlab_core.publishing.affiliate_reply.requests") as mock_req:
            post_affiliate_reply(
                "instagram", "id123", {"affiliate_url": "u", "affiliate_product": ""}, "gaming"
            )
        mock_req.post.assert_not_called()

    def test_youtube_is_noop(self) -> None:
        """YouTube affiliate URL is already in the video description (injected
        by cta_engine). post_affiliate_reply must NOT make a separate call —
        the noop branch is the contract."""
        with patch("genlab_core.publishing.affiliate_reply.requests") as mock_req:
            post_affiliate_reply(
                "youtube", "vid123", {"affiliate_url": "u", "affiliate_product": "p"}, "gaming"
            )
        mock_req.post.assert_not_called()

    def test_unknown_platform_is_noop(self) -> None:
        """An unknown platform is silently skipped (not a crash) — the
        affiliate flow is best-effort, not a publish gate."""
        with (
            patch("genlab_core.publishing.affiliate_reply.requests") as mock_req,
            patch("genlab_core.publishing.affiliate_reply.FacebookClient") as mock_fb,
            patch("genlab_core.publishing.affiliate_reply.XTwitterClient") as mock_x,
            patch("genlab_core.publishing.affiliate_reply.ThreadsClient") as mock_th,
        ):
            post_affiliate_reply(
                "myspace", "id123", {"affiliate_url": "u", "affiliate_product": "p"}, "gaming"
            )
        mock_req.post.assert_not_called()
        mock_fb.assert_not_called()
        mock_x.assert_not_called()
        mock_th.assert_not_called()


# ---------------------------------------------------------------------------
# Instagram branch — Graph API direct call.
# ---------------------------------------------------------------------------


class TestInstagramBranch:
    @patch("genlab_core.publishing.affiliate_reply.requests")
    @patch("genlab_core.publishing.affiliate_reply.resolve_meta_credentials")
    def test_posts_to_graph_api_with_correct_payload(self, mock_creds, mock_req) -> None:
        mock_creds.return_value = {"ig_access_token": "TOKEN"}
        mock_req.post.return_value = MagicMock(status_code=200, text="ok")

        post_affiliate_reply(
            "instagram",
            "media_999",
            {"affiliate_url": "https://x/y", "affiliate_product": "MyThing"},
            "gaming",
        )

        mock_req.post.assert_called_once()
        url = mock_req.post.call_args.args[0]
        # MUST use graph.facebook.com (NEVER graph.instagram.com) — security
        # rule, EAA page tokens are rejected by the IG-specific surface.
        # Use ``startswith`` here, not ``in``: an ``in`` host check would let
        # ``https://evil/?x=graph.facebook.com`` pass and CodeQL correctly
        # flags it (py/incomplete-url-substring-sanitization). The assertion
        # below is what real URL validation would look like.
        assert url.startswith("https://graph.facebook.com/"), url
        assert "graph.instagram.com" not in url
        assert "/media_999/comments" in url
        # Affiliate text format: "🔗 {product}: {url}".
        data = mock_req.post.call_args.kwargs["data"]
        assert "🔗 MyThing: https://x/y" in data["message"]
        assert data["access_token"] == "TOKEN"

    @patch("genlab_core.publishing.affiliate_reply.requests")
    @patch("genlab_core.publishing.affiliate_reply.resolve_meta_credentials")
    def test_missing_meta_token_short_circuits(self, mock_creds, mock_req) -> None:
        mock_creds.return_value = {"ig_access_token": ""}
        post_affiliate_reply(
            "instagram", "m1", {"affiliate_url": "u", "affiliate_product": "p"}, "gaming"
        )
        mock_req.post.assert_not_called()

    @patch("genlab_core.publishing.affiliate_reply.requests")
    @patch("genlab_core.publishing.affiliate_reply.resolve_meta_credentials")
    def test_non_200_response_logs_but_does_not_raise(self, mock_creds, mock_req) -> None:
        """A 4xx/5xx from Graph is logged at DEBUG and dropped — never propagated."""
        mock_creds.return_value = {"ig_access_token": "TOKEN"}
        mock_req.post.return_value = MagicMock(status_code=400, text="bad request")
        # Must not raise.
        post_affiliate_reply(
            "instagram", "m1", {"affiliate_url": "u", "affiliate_product": "p"}, "gaming"
        )


# ---------------------------------------------------------------------------
# Facebook branch — FacebookClient.post_reply.
# ---------------------------------------------------------------------------


class TestFacebookBranch:
    @patch("genlab_core.publishing.affiliate_reply.FacebookClient")
    @patch("genlab_core.publishing.affiliate_reply.resolve_fb_credentials")
    def test_invokes_post_reply(self, mock_creds, mock_fb_cls) -> None:
        mock_creds.return_value = ("tok", "page123")
        fb_instance = MagicMock()
        mock_fb_cls.return_value = fb_instance
        post_affiliate_reply(
            "facebook", "post_888", {"affiliate_url": "u", "affiliate_product": "p"}, "gaming"
        )
        mock_fb_cls.assert_called_once_with(access_token="tok")
        fb_instance.post_reply.assert_called_once()
        assert fb_instance.post_reply.call_args.args[0] == "post_888"

    @patch("genlab_core.publishing.affiliate_reply.FacebookClient")
    @patch("genlab_core.publishing.affiliate_reply.resolve_fb_credentials")
    def test_missing_fb_token_short_circuits(self, mock_creds, mock_fb_cls) -> None:
        mock_creds.return_value = ("", "page123")
        post_affiliate_reply(
            "facebook", "post_1", {"affiliate_url": "u", "affiliate_product": "p"}, "gaming"
        )
        mock_fb_cls.assert_not_called()


# ---------------------------------------------------------------------------
# X / Twitter branch — both legacy names route to the same client.
# ---------------------------------------------------------------------------


class TestXTwitterBranch:
    @pytest.mark.parametrize("platform_name", ["twitter", "x_twitter"])
    @patch("genlab_core.publishing.affiliate_reply.XTwitterClient")
    @patch("genlab_core.publishing.affiliate_reply.resolve_twitter_credentials")
    def test_both_platform_names_resolve(self, mock_creds, mock_x_cls, platform_name: str) -> None:
        """R-46 parallel: both ``twitter`` (default platform-list name) and
        ``x_twitter`` (canonical) must route to the same X client. Otherwise
        the silent-drop bug returns — the default list uses ``twitter`` and
        a branch that only matched ``x_twitter`` would skip every X
        affiliate-comment in production."""
        mock_creds.return_value = {
            "api_key": "k",
            "api_secret": "s",
            "access_token": "at",
            "access_token_secret": "ats",
        }
        x_instance = MagicMock()
        mock_x_cls.return_value = x_instance
        post_affiliate_reply(
            platform_name,
            "tweet_42",
            {"affiliate_url": "u", "affiliate_product": "p"},
            "gaming",
        )
        mock_x_cls.assert_called_once_with(**mock_creds.return_value)
        x_instance.post_reply.assert_called_once()
        assert x_instance.post_reply.call_args.args[0] == "tweet_42"

    @patch("genlab_core.publishing.affiliate_reply.XTwitterClient")
    @patch("genlab_core.publishing.affiliate_reply.resolve_twitter_credentials")
    def test_missing_api_key_short_circuits(self, mock_creds, mock_x_cls) -> None:
        mock_creds.return_value = {"api_key": "", "api_secret": "s"}
        post_affiliate_reply(
            "twitter", "t1", {"affiliate_url": "u", "affiliate_product": "p"}, "gaming"
        )
        mock_x_cls.assert_not_called()


# ---------------------------------------------------------------------------
# Threads branch — ThreadsClient.post_reply.
# ---------------------------------------------------------------------------


class TestThreadsBranch:
    @patch("genlab_core.publishing.affiliate_reply.ThreadsClient")
    @patch("genlab_core.publishing.affiliate_reply.resolve_threads_credentials")
    def test_invokes_post_reply(self, mock_creds, mock_th_cls) -> None:
        mock_creds.return_value = ("tok", "u123")
        th_instance = MagicMock()
        mock_th_cls.return_value = th_instance
        post_affiliate_reply(
            "threads", "th_5", {"affiliate_url": "u", "affiliate_product": "p"}, "gaming"
        )
        mock_th_cls.assert_called_once_with(access_token="tok", user_id="u123")
        th_instance.post_reply.assert_called_once()

    @patch("genlab_core.publishing.affiliate_reply.ThreadsClient")
    @patch("genlab_core.publishing.affiliate_reply.resolve_threads_credentials")
    def test_missing_user_id_short_circuits(self, mock_creds, mock_th_cls) -> None:
        mock_creds.return_value = ("tok", "")
        post_affiliate_reply(
            "threads", "th_1", {"affiliate_url": "u", "affiliate_product": "p"}, "gaming"
        )
        mock_th_cls.assert_not_called()


# ---------------------------------------------------------------------------
# Exception guard — main post has already shipped; we must NEVER unwind it.
# ---------------------------------------------------------------------------


class TestNeverPropagates:
    @patch("genlab_core.publishing.affiliate_reply.requests")
    @patch("genlab_core.publishing.affiliate_reply.resolve_meta_credentials")
    def test_ig_network_error_swallowed(self, mock_creds, mock_req) -> None:
        mock_creds.return_value = {"ig_access_token": "TOKEN"}
        mock_req.post.side_effect = ConnectionError("DNS failure")
        # Must NOT raise — the main post has already shipped at this point.
        post_affiliate_reply(
            "instagram", "m1", {"affiliate_url": "u", "affiliate_product": "p"}, "gaming"
        )

    @patch("genlab_core.publishing.affiliate_reply.FacebookClient")
    @patch("genlab_core.publishing.affiliate_reply.resolve_fb_credentials")
    def test_fb_client_error_swallowed(self, mock_creds, mock_fb_cls) -> None:
        mock_creds.return_value = ("tok", "page")
        mock_fb_cls.return_value.post_reply.side_effect = RuntimeError("permission")
        post_affiliate_reply(
            "facebook", "p1", {"affiliate_url": "u", "affiliate_product": "p"}, "gaming"
        )

    @patch("genlab_core.publishing.affiliate_reply.XTwitterClient")
    @patch("genlab_core.publishing.affiliate_reply.resolve_twitter_credentials")
    def test_x_client_error_swallowed(self, mock_creds, mock_x_cls) -> None:
        mock_creds.return_value = {"api_key": "k", "api_secret": "s"}
        mock_x_cls.return_value.post_reply.side_effect = Exception("rate limit")
        post_affiliate_reply(
            "twitter", "t1", {"affiliate_url": "u", "affiliate_product": "p"}, "gaming"
        )

    @patch("genlab_core.publishing.affiliate_reply.ThreadsClient")
    @patch("genlab_core.publishing.affiliate_reply.resolve_threads_credentials")
    def test_threads_client_error_swallowed(self, mock_creds, mock_th_cls) -> None:
        mock_creds.return_value = ("tok", "u")
        mock_th_cls.return_value.post_reply.side_effect = ValueError("oops")
        post_affiliate_reply(
            "threads", "t1", {"affiliate_url": "u", "affiliate_product": "p"}, "gaming"
        )
