"""R-29: the IG reel publish must finish within a TOTAL wall-clock budget that
stays under the publisher's 600s per-platform executor timeout — otherwise a
timed-out future records the platform FAILED while an orphaned thread keeps
polling and can post the reel anyway (phantom/duplicate publish).
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from genlab_core.platforms.instagram import (
    _RETRY_MIN_REMAINING_SECONDS,
    _TOTAL_PUBLISH_BUDGET_SECONDS,
    InstagramClient,
)

# The publisher's per-platform timeout (publish_all_platforms.py:897).
_EXECUTOR_TIMEOUT = 600


@pytest.fixture
def client() -> InstagramClient:
    return InstagramClient(access_token="EAA_TEST", ig_user_id="17841400000000001")


def test_total_budget_stays_under_executor_timeout() -> None:
    # The core invariant: even the worst-case publish must return before the
    # future times out, so the thread can never post after a recorded FAILED.
    assert _TOTAL_PUBLISH_BUDGET_SECONDS < _EXECUTOR_TIMEOUT


def test_retry_skipped_when_budget_exhausted(client: InstagramClient) -> None:
    with (
        patch.object(client, "_create_reel_container", return_value="cid"),
        patch.object(client, "_poll_container_status", return_value=None) as mock_poll,
        patch("genlab_core.platforms.instagram.time.sleep") as mock_sleep,
    ):
        # Only 30s of budget left — below the retry minimum, so NO second poll.
        result = client._publish_reel(
            video_url="https://cdn.test/x.mp4",
            caption="hi",
            _deadline=time.monotonic() + 30,
        )

    assert result is None
    assert mock_poll.call_count == 1  # retry was skipped
    mock_sleep.assert_not_called()


def test_retry_runs_when_budget_remains(client: InstagramClient) -> None:
    with (
        patch.object(client, "_create_reel_container", return_value="cid"),
        patch.object(client, "_poll_container_status", return_value=None) as mock_poll,
        patch("genlab_core.platforms.instagram.time.sleep") as mock_sleep,
    ):
        # Plenty of budget — the one allowed retry should fire.
        result = client._publish_reel(
            video_url="https://cdn.test/x.mp4",
            caption="hi",
            _deadline=time.monotonic() + 300,
        )

    assert result is None
    assert mock_poll.call_count == 2  # initial + one retry
    mock_sleep.assert_called_once_with(30)


def test_poll_budget_capped_to_remaining_deadline(client: InstagramClient) -> None:
    with (
        patch.object(client, "_create_reel_container", return_value="cid"),
        patch.object(client, "_poll_container_status", return_value=False) as mock_poll,
        patch.object(client, "_media_publish", return_value="post_123"),
    ):
        # 100s left — the poll must be capped to ~100, not the full 480 default.
        client._publish_reel(
            video_url="https://cdn.test/x.mp4",
            caption="hi",
            _deadline=time.monotonic() + 100,
        )

    passed_budget = mock_poll.call_args.kwargs["max_poll_seconds"]
    assert 0 < passed_budget <= 100


def test_retry_minimum_is_sane() -> None:
    # The retry threshold must leave room for the 30s backoff plus a poll.
    assert _RETRY_MIN_REMAINING_SECONDS > 30


# ── 2026-07-08: shared-deadline across CDN-provider retries ────────────
# Pins the fix for task #572 — the CDN retry loop in ``publish()`` used to
# spin up a fresh 540s ``_TOTAL_PUBLISH_BUDGET_SECONDS`` deadline on EACH
# call to ``_publish_reel``, so 2 CDN retries could stack to 1080s + upload
# time and blow past the 600s executor timeout in
# ``parallel_publish.execute_parallel_publish``. Live-fire on 2026-07-07
# hit this exactly — 2 of 5 niches recorded "Publish timed out after 600s
# for instagram" and anime never got processed because the 30-min service
# TimeoutSec expired first (see task #570 / task #572 / commit 7562337e).
#
# Fix: publish() creates a single ``shared_deadline`` before the CDN loop
# and passes it into every ``_publish_reel`` call — fast-path (URL supplied
# by caller) AND local-path (CDN upload required). The loop also short-
# circuits before uploading to a next provider if <90s of budget remain.


def test_publish_creates_shared_deadline_for_fast_path_url() -> None:
    """When the caller supplies a public URL directly, publish() must
    pass its shared deadline through so nested _publish_reel calls honor
    the SAME budget rather than each starting a fresh 540s clock."""
    from genlab_core.platforms.models import PublishPayload

    client = InstagramClient(access_token="EAA_TEST", ig_user_id="17841400000000001")
    payload = PublishPayload(
        caption="hi",
        media_paths=["https://cdn.test/x.mp4"],
        media_type="video",
        hashtags=[],
        hook="test hook",
        niche_id="test",
    )

    with patch.object(client, "_publish_reel", return_value="post_123") as mock_reel:
        client.publish(payload)

    # The single _publish_reel call must have received a _deadline kwarg.
    assert mock_reel.call_count == 1
    kwargs = mock_reel.call_args.kwargs
    assert "_deadline" in kwargs, (
        "publish() must pass a shared _deadline into _publish_reel — "
        "otherwise the reel call defaults to its own 540s clock. Task #572."
    )
    # It must be a monotonic-clock deadline within ~540s of now.
    delta = kwargs["_deadline"] - time.monotonic()
    assert 0 < delta <= _TOTAL_PUBLISH_BUDGET_SECONDS + 1


def test_publish_shares_deadline_across_cdn_retries() -> None:
    """The 2 CDN provider iterations must SHARE a single deadline. Task
    #572 regression pin: without a shared deadline, 2 CDN retries × 540s
    per _publish_reel call = 1080s > 600s executor timeout, causing the
    2026-07-07 IG hangs that starved anime."""
    from genlab_core.platforms.cdn_upload import CdnUploadResult
    from genlab_core.platforms.models import PublishPayload

    client = InstagramClient(access_token="EAA_TEST", ig_user_id="17841400000000001")
    payload = PublishPayload(
        caption="hi",
        media_paths=["/local/file.mp4"],  # triggers CDN path
        media_type="video",
        hashtags=[],
        hook="test hook",
        niche_id="test",
    )

    # 2 CDN attempts, first _publish_reel fails with 2207077 (Meta fetch
    # error → forces provider swap), second succeeds.
    cdn_results = [
        CdnUploadResult(url="https://litterbox.test/x.mp4", provider="litterbox", size_mb=1.0),
        CdnUploadResult(url="https://tmpfiles.test/x.mp4", provider="tmpfiles", size_mb=1.0),
    ]

    deadlines_seen: list[float] = []

    def fake_publish_reel(**kwargs):
        # Verify _deadline is passed AND capture its value so we can assert
        # BOTH iterations see the SAME deadline (not fresh 540s clocks each).
        assert "_deadline" in kwargs, "publish() must pass _deadline"
        deadlines_seen.append(kwargs["_deadline"])
        if len(deadlines_seen) == 1:
            client._last_error = "2207077 media upload failed"
            return None
        return "post_123"

    with (
        patch(
            "genlab_core.platforms.cdn_upload.upload_to_cdn_full",
            side_effect=cdn_results,
        ),
        patch.object(client, "_publish_reel", side_effect=fake_publish_reel),
    ):
        client.publish(payload)

    assert len(deadlines_seen) == 2, (
        f"Expected 2 _publish_reel calls (initial + CDN retry), got {len(deadlines_seen)}"
    )
    assert deadlines_seen[0] == deadlines_seen[1], (
        "CDN retries must share the SAME deadline value — task #572 "
        "regression. If each CDN attempt starts a fresh 540s clock, 2 "
        f"retries can stack past the 600s executor timeout. Got: "
        f"deadlines={deadlines_seen}"
    )


def test_publish_bails_when_budget_below_retry_min() -> None:
    """If the CDN retry loop only has <90s left when about to try the next
    provider, it must short-circuit with a clean error rather than start
    a fresh upload it can't finish. Prevents the orphaned-thread class
    documented in R-29."""
    from genlab_core.platforms.cdn_upload import CdnUploadResult
    from genlab_core.platforms.models import PublishPayload

    client = InstagramClient(access_token="EAA_TEST", ig_user_id="17841400000000001")
    payload = PublishPayload(
        caption="hi",
        media_paths=["/local/file.mp4"],
        media_type="video",
        hashtags=[],
        hook="test hook",
        niche_id="test",
    )

    def slow_first_reel(**kwargs):
        client._last_error = "2207077 CDN fetch error"
        return None

    # Force monotonic to advance so shared_deadline is nearly exhausted
    # before the second iteration. Sequence:
    #   1st call (publish() creating shared_deadline): 0.0
    #   2nd call (1st iteration's remaining-budget check): 1.0
    #     → shared_deadline = 0 + 540 = 540; remaining = 540 - 1 = 539 (OK)
    #   3rd+ call (2nd iteration's remaining-budget check): 500.0
    #     → remaining = 540 - 500 = 40 < 90 → bail
    with (
        patch(
            "genlab_core.platforms.cdn_upload.upload_to_cdn_full",
            side_effect=[
                CdnUploadResult(url="https://a.test/x.mp4", provider="litterbox", size_mb=1.0)
            ],
        ) as mock_upload,
        patch.object(client, "_publish_reel", side_effect=slow_first_reel),
        patch(
            "genlab_core.platforms.instagram.time.monotonic",
            side_effect=[0.0, 1.0, 500.0, 500.0, 500.0],
        ),
    ):
        result = client.publish(payload)

    assert mock_upload.call_count == 1, (
        "publish() must skip the 2nd CDN provider when <90s of budget "
        "remain — otherwise it stacks past the executor timeout. Got "
        f"{mock_upload.call_count} upload attempts."
    )
    assert not result.success
    assert "budget exhausted" in result.error, (
        f"Expected 'budget exhausted' in error message, got: {result.error!r}"
    )
