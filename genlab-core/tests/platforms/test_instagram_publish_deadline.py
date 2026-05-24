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
