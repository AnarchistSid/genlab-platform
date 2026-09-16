"""Pin the 2026-07-22 YouTube upload-scope check in check_youtube.

History: `check_youtube` verified the OAuth token by calling
`youtube.channels().list(part="id,statistics", mine=True)` and returning
"healthy" if any channel came back. But `channels.list(mine=True)`
requires only `youtube.readonly` — a completely different scope than
`youtube.upload`. If Google revokes the upload grant while keeping
readonly (rare but possible on OAuth re-consent flows or scope
downgrades), the health check would report green while every publish
would 403 with insufficientPermissions.

Same class-of-bug as the IG business_account fix earlier today: token
is *authenticated* != token can *actually publish*.

Fixed: after the channel probe passes, query Google's tokeninfo
endpoint and assert at least one of `youtube` / `youtube.force-ssl` /
`youtube.upload` is in the granted scope list. If tokeninfo itself
fails, fail-open (matches the FB debug_token scope-check pattern —
a broken side-channel MUST NOT trip a false alarm).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from genlab_core.monitoring.token_health import (
    _YOUTUBE_UPLOAD_SCOPES,
    _yt_token_has_upload_scope,
    check_youtube,
)


class TestYTUploadScopeConstant:
    def test_upload_scopes_frozenset_shape(self) -> None:
        """The three upload-capable scopes must all be recognized."""
        assert "https://www.googleapis.com/auth/youtube.upload" in _YOUTUBE_UPLOAD_SCOPES
        assert "https://www.googleapis.com/auth/youtube.force-ssl" in _YOUTUBE_UPLOAD_SCOPES
        assert "https://www.googleapis.com/auth/youtube" in _YOUTUBE_UPLOAD_SCOPES
        # Read-only MUST NOT be treated as upload-capable
        assert "https://www.googleapis.com/auth/youtube.readonly" not in _YOUTUBE_UPLOAD_SCOPES


class TestTokeninfoHelper:
    def test_helper_returns_true_when_upload_scope_granted(self) -> None:
        """youtube.upload alone → has_upload=True."""
        resp = MagicMock()
        resp.json.return_value = {
            "scope": "https://www.googleapis.com/auth/youtube.upload openid",
        }
        with patch("requests.get", return_value=resp):
            has_upload, granted = _yt_token_has_upload_scope("dummy-token")
        assert has_upload is True
        assert "https://www.googleapis.com/auth/youtube.upload" in granted

    def test_helper_returns_false_when_only_readonly(self) -> None:
        """youtube.readonly alone → has_upload=False. This is the exact
        scope-downgrade class-of-bug we're guarding against."""
        resp = MagicMock()
        resp.json.return_value = {
            "scope": "https://www.googleapis.com/auth/youtube.readonly openid",
        }
        with patch("requests.get", return_value=resp):
            has_upload, granted = _yt_token_has_upload_scope("dummy-token")
        assert has_upload is False
        assert granted == [
            "https://www.googleapis.com/auth/youtube.readonly",
            "openid",
        ]

    def test_helper_fails_open_on_network_error(self) -> None:
        """Fails open (returns True, []). Sentinel: empty granted list means
        the caller couldn't verify — matches FB debug_token pattern where
        a broken side-channel MUST NOT trip a token-health false alarm."""
        import requests as _r

        with patch("requests.get", side_effect=_r.RequestException("timeout")):
            has_upload, granted = _yt_token_has_upload_scope("dummy-token")
        assert has_upload is True
        assert granted == []


class TestCheckYoutubeUploadScopeIntegration:
    def test_token_exchange_path_missing_upload_scope_returns_error(
        self, monkeypatch
    ) -> None:
        """Token-exchange fallback path: refresh succeeds, but response `scope`
        omits any upload-capable scope. Must return status=error, not healthy."""
        monkeypatch.setenv("YOUTUBE_CLIENT_ID", "id")
        monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "secret")
        monkeypatch.setenv("YOUTUBE_REFRESH_TOKEN", "refresh")

        token_resp = MagicMock()
        token_resp.json.return_value = {
            "access_token": "at_xyz",
            "scope": "https://www.googleapis.com/auth/youtube.readonly",
        }
        # Force the fallback path by making google-auth import fail
        import builtins

        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name.startswith("google.oauth2") or name.startswith("googleapiclient"):
                raise ImportError(f"blocked {name}")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_fake_import):
            with patch("requests.post", return_value=token_resp):
                result = check_youtube()

        assert result["status"] == "error", (
            f"Missing upload scope MUST return error, got: {result}"
        )
        assert "upload scope" in result["message"].lower() or "re-authorize" in result["message"].lower()

    def test_token_exchange_path_with_upload_scope_returns_healthy(
        self, monkeypatch
    ) -> None:
        """Happy path regression: token-exchange fallback returns healthy
        when refresh succeeds AND upload scope is granted."""
        monkeypatch.setenv("YOUTUBE_CLIENT_ID", "id")
        monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "secret")
        monkeypatch.setenv("YOUTUBE_REFRESH_TOKEN", "refresh")

        token_resp = MagicMock()
        token_resp.json.return_value = {
            "access_token": "at_xyz",
            "scope": (
                "https://www.googleapis.com/auth/youtube.force-ssl "
                "https://www.googleapis.com/auth/yt-analytics.readonly"
            ),
        }
        import builtins

        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name.startswith("google.oauth2") or name.startswith("googleapiclient"):
                raise ImportError(f"blocked {name}")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_fake_import):
            with patch("requests.post", return_value=token_resp):
                result = check_youtube()

        assert result["status"] == "healthy", (
            f"Valid token with force-ssl MUST return healthy, got: {result}"
        )
        assert "https://www.googleapis.com/auth/youtube.force-ssl" in result.get(
            "granted_scopes", []
        )
