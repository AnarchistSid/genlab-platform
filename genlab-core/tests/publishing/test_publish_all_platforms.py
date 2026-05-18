"""Tests for the canonical multi-platform publisher.

Mocks all external dependencies: BacklogClient, platform clients, DailyCapEnforcer.
"""
from __future__ import annotations

import json
import sys
import tempfile
import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Module-level real video file (>= 10KB) for tests that need valid media paths
# ---------------------------------------------------------------------------
# Production code checks: file exists AND size >= 10KB
# We create a real temp file at module load time so all tests can use it.
_TEMP_VIDEO_DIR = tempfile.mkdtemp(prefix="genlab_test_")
_TEMP_VIDEO_PATH = os.path.join(_TEMP_VIDEO_DIR, "video.mp4")
with open(_TEMP_VIDEO_PATH, "wb") as _f:
    _f.write(b"\x00" * 15000)  # 15KB — exceeds the 10KB minimum


@pytest.fixture
def video_file():
    """Return path to a real temp video file that passes all production checks."""
    return _TEMP_VIDEO_PATH

# ---------------------------------------------------------------------------
# Ensure genlab_core is importable from the worktree's src layout
# ---------------------------------------------------------------------------
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from genlab_core.platforms.models import PublishPayload, PublishResult
from genlab_core.publishing.publish_all_platforms import (
    EXIT_ALL_FAILED,
    EXIT_DAILY_CAP,
    EXIT_NO_BLUEPRINTS,
    EXIT_SUCCESS,
    PidLock,
    build_payload,
    run_publish,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_blueprint(
    record_id: str = "rec-001",
    niche_id: str = "gaming",
    status: str = "VISUAL_READY",
    priority_score: float = 0.8,
    hook: str = "Insane clutch play wins the tournament",
    caption: str = "Epic gaming moment you need to see. Follow for more!",
    hashtags: str = "#gaming #esports #clutch",
    visual_paths: list | None = None,
    format: str = "reel",
    youtube_content: str = "",
    twitter_content: str = "",
    platform_publish_status: str = "",
    action_taken: str = "approved",
    scheduled_for: str = "",
    candidate_id: str = "cand-abc123",
    video_path: str | None = None,
) -> dict[str, Any]:
    if visual_paths is None:
        visual_paths = [video_path if video_path is not None else _TEMP_VIDEO_PATH]
    return {
        "id": record_id,
        "fields": {
            "niche_id": niche_id,
            "status": status,
            "priority_score": priority_score,
            "hook": hook,
            "caption": caption,
            "hashtags": hashtags,
            "visual_paths": json.dumps(visual_paths),
            "format": format,
            "youtube_content": youtube_content,
            "twitter_content": twitter_content,
            "platform_publish_status": platform_publish_status,
            "action_taken": action_taken,
            "scheduled_for": scheduled_for,
            "candidate_id": candidate_id,
        },
    }


def _make_client_mock(blueprints: list | None = None) -> MagicMock:
    """Create a mock BacklogClient that returns the given blueprints."""
    client = MagicMock()
    client.get_blueprints_by_status.return_value = blueprints or []
    client.blueprints.update.return_value = {}
    return client


def _make_cap_enforcer(can_publish: bool = True) -> MagicMock:
    enforcer = MagicMock()
    enforcer.can_publish.return_value = can_publish
    enforcer.record_publish.return_value = None
    enforcer.log_headroom.return_value = None
    return enforcer


def _success_result(platform: str) -> PublishResult:
    return PublishResult(
        platform=platform,
        success=True,
        post_id=f"{platform}-post-123",
        post_url=f"https://{platform}.com/post/123",
    )


def _failure_result(platform: str, error: str = "API error") -> PublishResult:
    return PublishResult(platform=platform, success=False, error=error)


# ---------------------------------------------------------------------------
# PidLock tests
# ---------------------------------------------------------------------------


class TestPidLock:
    def test_acquire_and_release(self, tmp_path):
        lock = PidLock("test_niche", lock_dir=tmp_path)
        assert lock.acquire() is True
        assert lock.path.exists()
        lock.release()
        assert not lock.path.exists()

    def test_double_acquire_same_process(self, tmp_path):
        lock = PidLock("test_niche", lock_dir=tmp_path)
        assert lock.acquire() is True
        # Same process — should detect as alive and return False
        lock2 = PidLock("test_niche", lock_dir=tmp_path)
        assert lock2.acquire() is False
        lock.release()

    def test_stale_lock_detection(self, tmp_path):
        lock_path = tmp_path / "publisher-stale.lock"
        # Write a PID that doesn't exist (99999999 is very unlikely to be alive)
        lock_path.write_text("99999999")
        lock = PidLock("stale", lock_dir=tmp_path)
        assert lock.acquire() is True
        lock.release()

    def test_release_missing_ok(self, tmp_path):
        lock = PidLock("test_niche", lock_dir=tmp_path)
        # Should not raise even though lock was never acquired
        lock.release()


# ---------------------------------------------------------------------------
# build_payload tests
# ---------------------------------------------------------------------------


class TestBuildPayload:
    def test_basic_payload(self):
        bp = _make_blueprint()
        payload = build_payload(bp["fields"], "instagram")
        assert isinstance(payload, PublishPayload)
        assert payload.niche_id == "gaming"
        assert payload.media_type == "video"
        assert payload.hook == "Insane clutch play wins the tournament"
        assert len(payload.media_paths) == 1

    def test_youtube_specific(self):
        """Plain-string youtube_content path (new contract).

        ``shorts_title`` comes from the ``hook`` column; ``youtube_content``
        is the plain-text description (after affiliate CTA + disclosure
        injection by the CTA engine).
        """
        bp = _make_blueprint(
            youtube_content="Subnautica 2 just dropped — Twitch is on fire.\n\n#affiliate",
        )
        payload = build_payload(bp["fields"], "youtube")
        assert payload.platform_specific is not None
        assert payload.platform_specific.shorts_title == (
            "Insane clutch play wins the tournament"
        )
        assert (
            "Subnautica 2 just dropped"
            in payload.platform_specific.community_post_text
        )

    def test_youtube_legacy_json_field_still_publishable(self):
        """Backward compat: pre-fix rows stored youtube_content as JSON dict.

        New publisher must defensively parse those legacy values and use
        the embedded description; the new hook column still wins for the
        Shorts title.
        """
        legacy = json.dumps({
            "title": "Did this clutch win it all?",
            "description": "Full breakdown of the play.",
        })
        bp = _make_blueprint(youtube_content=legacy)
        payload = build_payload(bp["fields"], "youtube")
        assert payload.platform_specific is not None
        # Hook drives shorts_title under the new contract
        assert payload.platform_specific.shorts_title == (
            "Insane clutch play wins the tournament"
        )
        # Legacy JSON description is unwrapped for the community post text
        assert (
            "Full breakdown of the play."
            in payload.platform_specific.community_post_text
        )

    def test_twitter_specific(self):
        tw_content = json.dumps({
            "routing": "single",
            "tweet_text": "Insane clutch play!",
        })
        bp = _make_blueprint(twitter_content=tw_content)
        payload = build_payload(bp["fields"], "twitter")
        assert payload.platform_specific is not None
        assert payload.platform_specific.tweet_text == "Insane clutch play!"
        assert payload.platform_specific.routing == "single"

    def test_facebook_specific(self):
        bp = _make_blueprint()
        payload = build_payload(bp["fields"], "facebook")
        from genlab_core.platforms.models import FacebookSpecific
        assert isinstance(payload.platform_specific, FacebookSpecific)

    def test_threads_specific(self):
        bp = _make_blueprint()
        payload = build_payload(bp["fields"], "threads")
        from genlab_core.platforms.models import ThreadsSpecific
        assert isinstance(payload.platform_specific, ThreadsSpecific)

    def test_hashtags_from_string(self):
        bp = _make_blueprint(hashtags="#gaming #esports #clutch")
        payload = build_payload(bp["fields"], "instagram")
        # Hashtags now keep # prefix (Sprint 67 fix)
        assert "#gaming" in payload.hashtags
        assert "#esports" in payload.hashtags

    def test_empty_visual_paths_raises(self):
        # Production code raises ValueError when format=reel but no valid media files
        bp = _make_blueprint(visual_paths=[])
        with pytest.raises(ValueError, match="No valid media files"):
            build_payload(bp["fields"], "instagram")

    def test_invalid_niche_id_raises(self):
        bp = _make_blueprint(niche_id="unknown_niche")
        with pytest.raises(ValueError, match="unknown niche_id"):
            build_payload(bp["fields"], "instagram")

    def test_ai_tech_normalizes_to_ai_creators(self):
        bp = _make_blueprint(niche_id="ai_tech")
        payload = build_payload(bp["fields"], "instagram")
        assert payload.niche_id == "ai_creators"


# ---------------------------------------------------------------------------
# run_publish integration tests (mocked externals)
# ---------------------------------------------------------------------------


_CRED_PATCH = "genlab_core.publishing.publish_all_platforms._resolve_client_kwargs"
_CLIENT_PATCH = "genlab_core.publishing.publish_all_platforms.get_client"
_RECORD_PATCH = "genlab_core.publishing.publish_all_platforms.record_publish"


class TestRunPublish:
    """Test the core run_publish() function with mocked dependencies."""

    def _patch_get_client(self, results: dict[str, PublishResult]):
        """Return a mock get_client that creates clients returning given results."""
        def _get_client(platform_id, **kwargs):
            client = MagicMock()
            if platform_id in results:
                client.publish.return_value = results[platform_id]
            else:
                client.publish.return_value = _failure_result(platform_id, "Not configured")
            return client
        return _get_client

    @patch(_RECORD_PATCH)
    @patch(_CLIENT_PATCH)
    @patch(_CRED_PATCH, return_value={})
    def test_success_single_platform(self, mock_creds, mock_get_client, mock_record):
        bp = _make_blueprint()
        client = _make_client_mock([bp])
        enforcer = _make_cap_enforcer(can_publish=True)

        mock_get_client.side_effect = self._patch_get_client({
            "instagram": _success_result("instagram"),
        })

        exit_code = run_publish(
            niche_id="gaming",
            backlog_client=client,
            daily_cap=enforcer,
            enabled_platforms=["instagram"],
        )
        assert exit_code == EXIT_SUCCESS
        # Blueprint should be updated to PUBLISHED
        client.blueprints.update.assert_called()

    @patch(_RECORD_PATCH)
    @patch(_CLIENT_PATCH)
    @patch(_CRED_PATCH, return_value={})
    def test_no_blueprints_returns_exit_1(self, mock_creds, mock_get_client, mock_record):
        client = _make_client_mock([])
        enforcer = _make_cap_enforcer()

        exit_code = run_publish(
            niche_id="gaming",
            backlog_client=client,
            daily_cap=enforcer,
            enabled_platforms=["instagram"],
        )
        assert exit_code == EXIT_NO_BLUEPRINTS

    @patch(_RECORD_PATCH)
    @patch(_CLIENT_PATCH)
    @patch(_CRED_PATCH, return_value={})
    def test_all_platforms_fail_returns_exit_2(self, mock_creds, mock_get_client, mock_record):
        bp = _make_blueprint()
        client = _make_client_mock([bp])
        enforcer = _make_cap_enforcer()

        mock_get_client.side_effect = self._patch_get_client({
            "instagram": _failure_result("instagram"),
        })

        exit_code = run_publish(
            niche_id="gaming",
            backlog_client=client,
            daily_cap=enforcer,
            enabled_platforms=["instagram"],
        )
        assert exit_code == EXIT_ALL_FAILED

    @patch(_RECORD_PATCH)
    @patch(_CLIENT_PATCH)
    @patch(_CRED_PATCH, return_value={})
    def test_daily_cap_reached_returns_exit_3(self, mock_creds, mock_get_client, mock_record):
        bp = _make_blueprint()
        client = _make_client_mock([bp])
        enforcer = _make_cap_enforcer(can_publish=False)

        exit_code = run_publish(
            niche_id="gaming",
            backlog_client=client,
            daily_cap=enforcer,
            enabled_platforms=["instagram"],
        )
        assert exit_code == EXIT_DAILY_CAP

    @patch(_RECORD_PATCH)
    @patch(_CLIENT_PATCH)
    @patch(_CRED_PATCH, return_value={})
    def test_multi_platform_partial_success(self, mock_creds, mock_get_client, mock_record):
        bp = _make_blueprint()
        client = _make_client_mock([bp])
        enforcer = _make_cap_enforcer()

        mock_get_client.side_effect = self._patch_get_client({
            "instagram": _success_result("instagram"),
            "youtube": _failure_result("youtube", "quota exceeded"),
        })

        exit_code = run_publish(
            niche_id="gaming",
            backlog_client=client,
            daily_cap=enforcer,
            enabled_platforms=["instagram", "youtube"],
        )
        # At least one succeeded
        assert exit_code == EXIT_SUCCESS

    @patch(_RECORD_PATCH)
    @patch(_CLIENT_PATCH)
    @patch(_CRED_PATCH, return_value={})
    def test_top1_by_priority_score(self, mock_creds, mock_get_client, mock_record):
        bp_low = _make_blueprint(record_id="rec-low", priority_score=0.3)
        bp_high = _make_blueprint(record_id="rec-high", priority_score=0.9)
        client = _make_client_mock([bp_low, bp_high])
        enforcer = _make_cap_enforcer()

        published_payloads = []

        def _capture_get_client(platform_id, **kwargs):
            c = MagicMock()
            def _pub(payload):
                published_payloads.append(payload)
                return _success_result(platform_id)
            c.publish.side_effect = _pub
            return c

        mock_get_client.side_effect = _capture_get_client

        exit_code = run_publish(
            niche_id="gaming",
            backlog_client=client,
            daily_cap=enforcer,
            enabled_platforms=["instagram"],
        )
        assert exit_code == EXIT_SUCCESS
        # Should only publish 1 blueprint (the highest priority one)
        assert len(published_payloads) == 1

    @patch(_RECORD_PATCH)
    @patch(_CLIENT_PATCH)
    @patch(_CRED_PATCH, return_value={})
    def test_niche_mismatch_filtered_out(self, mock_creds, mock_get_client, mock_record):
        """Blueprints with wrong niche_id are silently filtered before selection."""
        bp_wrong = _make_blueprint(niche_id="movies")
        bp_right = _make_blueprint(niche_id="gaming", record_id="rec-right")
        client = _make_client_mock([bp_wrong, bp_right])
        enforcer = _make_cap_enforcer()

        mock_get_client.side_effect = self._patch_get_client({
            "instagram": _success_result("instagram"),
        })

        exit_code = run_publish(
            niche_id="gaming",
            backlog_client=client,
            daily_cap=enforcer,
            enabled_platforms=["instagram"],
        )
        assert exit_code == EXIT_SUCCESS

    @patch(_RECORD_PATCH)
    @patch(_CLIENT_PATCH)
    @patch(_CRED_PATCH, return_value={})
    def test_status_set_to_publishing_before_attempts(self, mock_creds, mock_get_client, mock_record):
        bp = _make_blueprint()
        client = _make_client_mock([bp])
        enforcer = _make_cap_enforcer()

        update_calls = []

        def _track_update(record_id, fields, **kwargs):
            update_calls.append(fields.copy())
            return {}

        client.blueprints.update.side_effect = _track_update
        mock_get_client.side_effect = self._patch_get_client({
            "instagram": _success_result("instagram"),
        })

        run_publish(
            niche_id="gaming",
            backlog_client=client,
            daily_cap=enforcer,
            enabled_platforms=["instagram"],
        )

        # First update should set PUBLISHING, second should set PUBLISHED
        assert len(update_calls) >= 2
        first_update = update_calls[0]
        assert first_update.get("status") == "PUBLISHING"

    @patch(_RECORD_PATCH)
    @patch(_CLIENT_PATCH)
    @patch(_CRED_PATCH, return_value={})
    def test_record_publish_called_for_each_platform(self, mock_creds, mock_get_client, mock_record):
        bp = _make_blueprint()
        client = _make_client_mock([bp])
        enforcer = _make_cap_enforcer()

        mock_get_client.side_effect = self._patch_get_client({
            "instagram": _success_result("instagram"),
            "youtube": _failure_result("youtube"),
        })

        run_publish(
            niche_id="gaming",
            backlog_client=client,
            daily_cap=enforcer,
            enabled_platforms=["instagram", "youtube"],
        )

        # Should record analytics for each platform attempt
        assert mock_record.call_count == 2

    @patch(_RECORD_PATCH)
    @patch(_CLIENT_PATCH)
    @patch(_CRED_PATCH, return_value={})
    def test_gatekeeper_blocks_low_score(self, mock_creds, mock_get_client, mock_record):
        """Blueprint with priority_score below floor is filtered."""
        bp = _make_blueprint(priority_score=0.1)
        client = _make_client_mock([bp])
        enforcer = _make_cap_enforcer()

        exit_code = run_publish(
            niche_id="gaming",
            backlog_client=client,
            daily_cap=enforcer,
            enabled_platforms=["instagram"],
        )
        # Score below 0.3 floor -> gatekeeper blocks -> no eligible -> exit 1
        assert exit_code == EXIT_NO_BLUEPRINTS

    @patch(_RECORD_PATCH)
    @patch(_CLIENT_PATCH)
    @patch(_CRED_PATCH, return_value=None)
    def test_credential_resolution_failure_skips_platform(self, mock_creds, mock_get_client, mock_record):
        """When _resolve_client_kwargs returns None, the platform is marked FAILED."""
        bp = _make_blueprint()
        client = _make_client_mock([bp])
        enforcer = _make_cap_enforcer()

        exit_code = run_publish(
            niche_id="gaming",
            backlog_client=client,
            daily_cap=enforcer,
            enabled_platforms=["instagram"],
        )
        assert exit_code == EXIT_ALL_FAILED
