"""Pin the per-provider CDN retry on Meta container error 2207077.

The bug class this guards against (verified against prod 2026-06-27):
- ai_creators Instagram failed 8/8 times in the last 60 days with error
  2207077 ("media upload failed"). Every failure was a litterbox.catbox.moe
  URL. In-client retry was correctly skipped on 2207077 per a code comment
  that promised "outer publisher loop will retry with a fresh CDN URL" —
  but that outer loop did not exist anywhere in the codebase, so every
  2207077 silently burned a publish slot.

The fix wraps the CDN upload + publish in a per-provider retry loop that
rotates between litterbox and tmpfiles. This file pins behaviour so a
future refactor can't drop the loop and reintroduce the silent failure.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from genlab_core.platforms.cdn_upload import (
    PROVIDER_LITTERBOX,
    PROVIDER_TMPFILES,
    CdnUploadResult,
)
from genlab_core.platforms.models import InstagramSpecific, PublishPayload

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ig_payload(media_path: str = "/tmp/clip.mp4") -> PublishPayload:
    return PublishPayload(
        caption="Test reel",
        media_paths=[Path(media_path)],
        media_type="video",
        hashtags=["#test"],
        hook="Watch this",
        niche_id="ai_creators",
        platform_specific=InstagramSpecific(share_to_feed=True),
    )


def _threads_payload(media_path: str = "/tmp/clip.mp4") -> PublishPayload:
    return PublishPayload(
        caption="Test thread",
        media_paths=[Path(media_path)],
        media_type="video",
        hashtags=[],
        hook="",
        niche_id="ai_creators",
    )


def _make_ig_client():
    from genlab_core.platforms.instagram import InstagramClient

    return InstagramClient(
        access_token="EAA_TEST_TOKEN",
        ig_user_id="17841400000000001",
        api_version="v21.0",
    )


def _make_threads_client():
    from genlab_core.platforms.threads import ThreadsClient

    return ThreadsClient(
        access_token="THREADS_TEST_TOKEN",
        user_id="123456789",
    )


# ---------------------------------------------------------------------------
# Instagram tests
# ---------------------------------------------------------------------------


class TestInstagramCdnRetryOn2207077(unittest.TestCase):
    def _set_publish_reel_results(self, ig_client, results):
        """Patch ``_publish_reel`` so each call returns the next value in
        ``results``. If a slot is a tuple ``(None, error_str)`` the None is
        returned AND ``_last_error`` is set to the string (mimics what the
        real method does on failure)."""
        iterator = iter(results)

        def _stub(*_args, **_kwargs):
            value = next(iterator)
            if isinstance(value, tuple):
                post_id, err = value
                ig_client._last_error = err
                return post_id
            ig_client._last_error = ""
            return value

        return patch.object(ig_client, "_publish_reel", side_effect=_stub)

    def test_litterbox_2207077_retries_with_tmpfiles(self):
        """First publish returns None with 2207077 → loop re-uploads using
        tmpfiles → second publish succeeds → SUCCESS result."""
        ig_client = _make_ig_client()

        def _fake_upload(_path, *, require_external, exclude_providers=frozenset()):
            assert require_external is True
            if PROVIDER_LITTERBOX not in exclude_providers:
                return CdnUploadResult(
                    url="https://litter.catbox.moe/abc.mp4",
                    provider=PROVIDER_LITTERBOX,
                    size_mb=5.0,
                )
            if PROVIDER_TMPFILES not in exclude_providers:
                return CdnUploadResult(
                    url="https://tmpfiles.org/dl/123/abc.mp4",
                    provider=PROVIDER_TMPFILES,
                    size_mb=5.0,
                )
            return None

        with (
            patch(
                "genlab_core.platforms.cdn_upload.upload_to_cdn_full",
                side_effect=_fake_upload,
            ) as mock_upload,
            self._set_publish_reel_results(
                ig_client,
                [(None, "Container processing error: media error 2207077"), "post_success_123"],
            ) as mock_publish,
        ):
            result = ig_client.publish(_ig_payload())

        assert result.success is True, f"expected SUCCESS, got error={result.error!r}"
        assert result.post_id == "post_success_123"
        assert mock_upload.call_count == 2, "should rotate to second provider"
        assert mock_publish.call_count == 2

        # Second upload call must explicitly exclude litterbox.
        _args, kwargs = mock_upload.call_args_list[1]
        assert PROVIDER_LITTERBOX in kwargs["exclude_providers"]

    def test_non_2207077_error_does_not_retry_cdn(self):
        """Non-2207077 errors (token invalid, etc.) don't benefit from
        rotating CDN — exit early."""
        ig_client = _make_ig_client()

        with (
            patch(
                "genlab_core.platforms.cdn_upload.upload_to_cdn_full",
                return_value=CdnUploadResult(
                    url="https://litter.catbox.moe/abc.mp4",
                    provider=PROVIDER_LITTERBOX,
                    size_mb=5.0,
                ),
            ) as mock_upload,
            self._set_publish_reel_results(
                ig_client,
                [(None, "OAuth token invalid: bad signature")],
            ) as mock_publish,
        ):
            result = ig_client.publish(_ig_payload())

        assert result.success is False
        assert "OAuth token invalid" in result.error
        assert mock_upload.call_count == 1, "should NOT rotate on non-2207077"
        assert mock_publish.call_count == 1

    def test_success_first_attempt_no_retry(self):
        """Happy path: publish succeeds with the first CDN URL."""
        ig_client = _make_ig_client()

        with (
            patch(
                "genlab_core.platforms.cdn_upload.upload_to_cdn_full",
                return_value=CdnUploadResult(
                    url="https://litter.catbox.moe/abc.mp4",
                    provider=PROVIDER_LITTERBOX,
                    size_mb=5.0,
                ),
            ) as mock_upload,
            self._set_publish_reel_results(ig_client, ["post_first_try"]) as mock_publish,
        ):
            # Also stub the permalink GET so we don't hit the network.
            with patch.object(ig_client, "_graph_get", return_value={}):
                result = ig_client.publish(_ig_payload())

        assert result.success is True
        assert result.post_id == "post_first_try"
        assert mock_upload.call_count == 1
        assert mock_publish.call_count == 1

    def test_both_providers_exhausted_returns_failed(self):
        """2207077 on every provider → FAILED with diagnostic message."""
        ig_client = _make_ig_client()

        def _fake_upload(_path, *, require_external, exclude_providers=frozenset()):
            if PROVIDER_LITTERBOX not in exclude_providers:
                return CdnUploadResult(
                    url="https://litter.catbox.moe/x.mp4",
                    provider=PROVIDER_LITTERBOX,
                    size_mb=5.0,
                )
            if PROVIDER_TMPFILES not in exclude_providers:
                return CdnUploadResult(
                    url="https://tmpfiles.org/dl/y.mp4",
                    provider=PROVIDER_TMPFILES,
                    size_mb=5.0,
                )
            return None

        with (
            patch(
                "genlab_core.platforms.cdn_upload.upload_to_cdn_full",
                side_effect=_fake_upload,
            ),
            self._set_publish_reel_results(
                ig_client,
                [
                    (None, "Container error: media 2207077 — upload failed"),
                    (None, "Container error: media 2207077 — upload failed"),
                ],
            ),
        ):
            result = ig_client.publish(_ig_payload())

        assert result.success is False
        assert "2207077 on all CDN providers" in result.error

    def test_cdn_upload_failure_no_publish_attempt(self):
        """When no provider can give us a URL the publish API is never called."""
        ig_client = _make_ig_client()

        with (
            patch(
                "genlab_core.platforms.cdn_upload.upload_to_cdn_full",
                return_value=None,
            ) as mock_upload,
            patch.object(ig_client, "_publish_reel") as mock_publish,
        ):
            result = ig_client.publish(_ig_payload())

        assert result.success is False
        assert "exhausted providers" in result.error
        assert mock_upload.call_count == 1
        mock_publish.assert_not_called()


# ---------------------------------------------------------------------------
# Threads tests
# ---------------------------------------------------------------------------


class TestThreadsCdnRetryOn2207077(unittest.TestCase):
    def test_threads_same_pattern(self):
        """Threads video publish rotates litterbox → tmpfiles on 2207077."""
        threads_client = _make_threads_client()

        def _fake_upload(_path, *, require_external, exclude_providers=frozenset()):
            assert require_external is True
            if PROVIDER_LITTERBOX not in exclude_providers:
                return CdnUploadResult(
                    url="https://litter.catbox.moe/abc.mp4",
                    provider=PROVIDER_LITTERBOX,
                    size_mb=5.0,
                )
            if PROVIDER_TMPFILES not in exclude_providers:
                return CdnUploadResult(
                    url="https://tmpfiles.org/dl/abc.mp4",
                    provider=PROVIDER_TMPFILES,
                    size_mb=5.0,
                )
            return None

        # _create_container returns the container_id; on the first call it
        # also pre-sets the error so a later _poll_container ERROR with 2207077
        # is observed by the outer loop. To keep the test simple we instead
        # let _create_container succeed and have _poll_container return ERROR
        # with the 2207077 message on the first call, FINISHED on the second.
        poll_calls = {"n": 0}

        def _stub_create(*_args, **_kwargs):
            return "ctr_test"

        def _stub_poll(_container_id, *_args, **_kwargs):
            poll_calls["n"] += 1
            if poll_calls["n"] == 1:
                threads_client._last_error = (
                    "container processing error: media upload failed (2207077)"
                )
                return "ERROR"
            threads_client._last_error = ""
            return "FINISHED"

        publish_calls = {"n": 0}

        def _stub_publish(*, container_id):
            publish_calls["n"] += 1
            return "thread_post_999"

        # Stub the permalink fetch (httpx-less): _build_publish_success calls
        # requests.get — patch it module-wide.
        with (
            patch(
                "genlab_core.platforms.cdn_upload.upload_to_cdn_full",
                side_effect=_fake_upload,
            ) as mock_upload,
            patch.object(
                threads_client, "_create_container", side_effect=_stub_create
            ) as mock_create,
            patch.object(threads_client, "_poll_container", side_effect=_stub_poll),
            patch.object(
                threads_client, "_threads_publish", side_effect=_stub_publish
            ) as mock_publish,
            patch(
                "genlab_core.platforms.threads.requests.get",
                return_value=MagicMock(ok=True, json=lambda: {"permalink": ""}),
            ),
        ):
            result = threads_client.publish(_threads_payload())

        assert result.success is True, f"got error={result.error!r}"
        assert result.post_id == "thread_post_999"
        assert mock_upload.call_count == 2, "Threads should rotate CDN on 2207077"
        assert mock_create.call_count == 2
        assert mock_publish.call_count == 1

        # Second upload call must exclude litterbox.
        _args, kwargs = mock_upload.call_args_list[1]
        assert PROVIDER_LITTERBOX in kwargs["exclude_providers"]


# ---------------------------------------------------------------------------
# cdn_upload module-level tests
# ---------------------------------------------------------------------------


class TestUploadToCdnFull(unittest.TestCase):
    def _temp_file(self, size_bytes: int = 1024) -> Path:
        f = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        try:
            f.write(b"x" * size_bytes)
        finally:
            f.close()
        return Path(f.name)

    def test_exclude_providers_param_in_upload_to_cdn_full(self):
        """exclude_providers={'litterbox'} → tmpfiles called, litterbox NOT."""
        from genlab_core.platforms import cdn_upload

        tmp = self._temp_file()
        try:
            with (
                patch.object(cdn_upload, "_serve_via_tunnel", return_value=None) as mock_tunnel,
                patch.object(
                    cdn_upload, "_upload_to_litterbox", return_value="https://litter/x"
                ) as mock_litter,
                patch.object(
                    cdn_upload,
                    "_upload_to_tmpfiles",
                    return_value="https://tmpfiles.org/dl/y",
                ) as mock_tmp,
            ):
                result = cdn_upload.upload_to_cdn_full(
                    tmp,
                    require_external=True,
                    exclude_providers=frozenset({PROVIDER_LITTERBOX}),
                )
        finally:
            tmp.unlink(missing_ok=True)

        assert result is not None
        assert result.provider == PROVIDER_TMPFILES
        assert result.url == "https://tmpfiles.org/dl/y"
        mock_litter.assert_not_called()
        mock_tmp.assert_called_once()
        # require_external=True still skips the tunnel.
        mock_tunnel.assert_not_called()

    def test_upload_to_cdn_backward_compat(self):
        """Old upload_to_cdn(...) still returns str|None unchanged."""
        from genlab_core.platforms import cdn_upload

        tmp = self._temp_file()
        try:
            with patch.object(cdn_upload, "_serve_via_tunnel", return_value="https://tunnel/x.mp4"):
                url = cdn_upload.upload_to_cdn(tmp)
            assert isinstance(url, str)
            assert url == "https://tunnel/x.mp4"

            # Returns None when nothing works.
            with (
                patch.object(cdn_upload, "_serve_via_tunnel", return_value=None),
                patch.object(cdn_upload, "_upload_to_litterbox", return_value=None),
                patch.object(cdn_upload, "_upload_to_tmpfiles", return_value=None),
            ):
                url = cdn_upload.upload_to_cdn(tmp)
            assert url is None
        finally:
            tmp.unlink(missing_ok=True)

    def test_upload_to_cdn_full_returns_size_and_provider(self):
        """Structured result carries the size + canonical provider ID."""
        from genlab_core.platforms import cdn_upload

        tmp = self._temp_file(size_bytes=2 * 1024 * 1024)  # 2 MB
        try:
            with (
                patch.object(cdn_upload, "_serve_via_tunnel", return_value=None),
                patch.object(
                    cdn_upload,
                    "_upload_to_litterbox",
                    return_value="https://litter.catbox.moe/abc.mp4",
                ),
            ):
                result = cdn_upload.upload_to_cdn_full(tmp, require_external=True)
        finally:
            tmp.unlink(missing_ok=True)

        assert result is not None
        assert result.provider == PROVIDER_LITTERBOX
        assert result.url == "https://litter.catbox.moe/abc.mp4"
        assert abs(result.size_mb - 2.0) < 0.01


if __name__ == "__main__":
    unittest.main()
