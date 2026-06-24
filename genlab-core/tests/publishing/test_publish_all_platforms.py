"""Tests for the canonical multi-platform publisher.

Mocks all external dependencies: BacklogClient, platform clients, DailyCapEnforcer.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
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
    _already_published_platforms,
    _preflight_token_refresh,
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
        assert payload.platform_specific.shorts_title == ("Insane clutch play wins the tournament")
        assert "Subnautica 2 just dropped" in payload.platform_specific.community_post_text

    def test_youtube_legacy_json_field_still_publishable(self):
        """Backward compat: pre-fix rows stored youtube_content as JSON dict.

        New publisher must defensively parse those legacy values and use
        the embedded description; the new hook column still wins for the
        Shorts title.
        """
        legacy = json.dumps(
            {
                "title": "Did this clutch win it all?",
                "description": "Full breakdown of the play.",
            }
        )
        bp = _make_blueprint(youtube_content=legacy)
        payload = build_payload(bp["fields"], "youtube")
        assert payload.platform_specific is not None
        # Hook drives shorts_title under the new contract
        assert payload.platform_specific.shorts_title == ("Insane clutch play wins the tournament")
        # Legacy JSON description is unwrapped for the community post text
        assert "Full breakdown of the play." in payload.platform_specific.community_post_text

    def test_twitter_specific(self):
        tw_content = json.dumps(
            {
                "routing": "single",
                "tweet_text": "Insane clutch play!",
            }
        )
        bp = _make_blueprint(twitter_content=tw_content)
        payload = build_payload(bp["fields"], "twitter")
        assert payload.platform_specific is not None
        assert payload.platform_specific.tweet_text == "Insane clutch play!"
        assert payload.platform_specific.routing == "single"

    def test_twitter_first_comment_set_for_legacy_name(self):
        """R-46: the affiliate first-comment must populate for the legacy
        platform name "twitter" (which the default platform list uses), not
        only "x_twitter" — previously it was silently dropped for "twitter",
        losing the entire X affiliate payload."""
        bp = _make_blueprint(twitter_content=json.dumps({"routing": "single", "tweet_text": "x"}))
        bp["fields"]["twitter_first_comment"] = "Get it: https://amzn.to/abc"
        for name in ("twitter", "x_twitter"):
            payload = build_payload(bp["fields"], name)
            assert payload.first_comment_text == "Get it: https://amzn.to/abc", (
                f"first_comment_text dropped for platform name {name!r}"
            )

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


# Refactor-#9 PR 6b/N moved the publish dispatch into parallel_publish.py;
# the orchestrator's local bindings are no longer reached by the publish
# code path. The three patches below must target the parallel_publish
# module-level bindings — that's where the calls actually happen now.
_CRED_PATCH = "genlab_core.publishing.parallel_publish.resolve_client_kwargs"
_CLIENT_PATCH = "genlab_core.publishing.parallel_publish.get_client"
_RECORD_PATCH = "genlab_core.publishing.parallel_publish.record_publish"

# Preflight has its own module-level bindings since refactor-#9 PR 4/N
# extracted ``resolve_client_kwargs`` + ``get_client`` into
# ``publishing.preflight``. The orchestrator-side patches above don't
# reach those calls; tests of the preflight path use the constants below.
_PREFLIGHT_CRED_PATCH = "genlab_core.publishing.preflight.resolve_client_kwargs"
_PREFLIGHT_CLIENT_PATCH = "genlab_core.publishing.preflight.get_client"


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

        mock_get_client.side_effect = self._patch_get_client(
            {
                "instagram": _success_result("instagram"),
            }
        )

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
    def test_lost_claim_skips_publish(self, mock_creds, mock_get_client, mock_record):
        """R-24: if the atomic VISUAL_READY->PUBLISHING claim is lost (another
        publisher already took the blueprint), the run must skip and publish
        nothing — preventing a double-publish to the live channel."""
        bp = _make_blueprint()
        client = _make_client_mock([bp])
        client.blueprints.claim_status.return_value = False  # another run won the claim
        enforcer = _make_cap_enforcer(can_publish=True)
        mock_get_client.side_effect = self._patch_get_client(
            {"instagram": _success_result("instagram")}
        )

        exit_code = run_publish(
            niche_id="gaming",
            backlog_client=client,
            daily_cap=enforcer,
            enabled_platforms=["instagram"],
        )
        assert exit_code == EXIT_NO_BLUEPRINTS
        client.blueprints.claim_status.assert_called_once()
        mock_get_client.assert_not_called()  # never attempted to publish

    @patch(_RECORD_PATCH)
    @patch(_CLIENT_PATCH)
    @patch(_CRED_PATCH, return_value={})
    def test_all_platforms_fail_returns_exit_2(self, mock_creds, mock_get_client, mock_record):
        bp = _make_blueprint()
        client = _make_client_mock([bp])
        enforcer = _make_cap_enforcer()

        mock_get_client.side_effect = self._patch_get_client(
            {
                "instagram": _failure_result("instagram"),
            }
        )

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

        mock_get_client.side_effect = self._patch_get_client(
            {
                "instagram": _success_result("instagram"),
                "youtube": _failure_result("youtube", "quota exceeded"),
            }
        )

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

        mock_get_client.side_effect = self._patch_get_client(
            {
                "instagram": _success_result("instagram"),
            }
        )

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
    def test_status_set_to_publishing_before_attempts(
        self, mock_creds, mock_get_client, mock_record
    ):
        bp = _make_blueprint()
        client = _make_client_mock([bp])
        enforcer = _make_cap_enforcer()

        update_calls = []

        def _track_update(record_id, fields, **kwargs):
            update_calls.append(fields.copy())
            return {}

        client.blueprints.update.side_effect = _track_update
        mock_get_client.side_effect = self._patch_get_client(
            {
                "instagram": _success_result("instagram"),
            }
        )

        run_publish(
            niche_id="gaming",
            backlog_client=client,
            daily_cap=enforcer,
            enabled_platforms=["instagram"],
        )

        # R-24: PUBLISHING is now claimed atomically (VISUAL_READY->PUBLISHING)
        # via claim_status BEFORE any publish attempt, so a crash mid-publish
        # leaves the row PUBLISHING for the recovery path. The terminal status
        # (PUBLISHED) is still written via update.
        client.blueprints.claim_status.assert_called_once()
        claim_call = client.blueprints.claim_status.call_args
        assert claim_call.kwargs.get("expected_status") == "VISUAL_READY"
        assert claim_call.kwargs.get("new_status") == "PUBLISHING"
        assert any(u.get("status") == "PUBLISHED" for u in update_calls), update_calls

    @patch(_RECORD_PATCH)
    @patch(_CLIENT_PATCH)
    @patch(_CRED_PATCH, return_value={})
    def test_record_publish_called_for_each_platform(
        self, mock_creds, mock_get_client, mock_record
    ):
        bp = _make_blueprint()
        client = _make_client_mock([bp])
        enforcer = _make_cap_enforcer()

        mock_get_client.side_effect = self._patch_get_client(
            {
                "instagram": _success_result("instagram"),
                "youtube": _failure_result("youtube"),
            }
        )

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
    def test_credential_resolution_failure_skips_platform(
        self, mock_creds, mock_get_client, mock_record
    ):
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


class TestAlreadyPublishedPlatforms:
    """R-29: the per-platform skip primitive — succeeded platforms must be
    detected from platform_publish_status so re-publishing never double-posts."""

    def test_empty_when_no_status(self):
        assert _already_published_platforms({}) == set()
        assert _already_published_platforms({"platform_publish_status": "{}"}) == set()

    def test_bare_string_published(self):
        fields = {"platform_publish_status": json.dumps({"instagram": "PUBLISHED"})}
        assert _already_published_platforms(fields) == {"instagram"}

    def test_dict_status_published(self):
        fields = {
            "platform_publish_status": json.dumps(
                {"youtube": {"status": "PUBLISHED", "post_id": "yt1"}}
            )
        }
        assert _already_published_platforms(fields) == {"youtube"}

    def test_mixed_published_and_failed(self):
        # IG live, YT failed — only IG is "already published" (YT must retry).
        fields = {
            "platform_publish_status": json.dumps(
                {
                    "instagram": "PUBLISHED",
                    "youtube": {"status": "FAILED", "attempts": 2},
                    "facebook": "FAILED",
                }
            )
        }
        assert _already_published_platforms(fields) == {"instagram"}

    def test_accepts_dict_value_not_just_json_string(self):
        fields = {"platform_publish_status": {"instagram": "PUBLISHED", "x_twitter": "FAILED"}}
        assert _already_published_platforms(fields) == {"instagram"}

    def test_malformed_is_safe(self):
        assert _already_published_platforms({"platform_publish_status": "{not json"}) == set()
        assert _already_published_platforms({"platform_publish_status": None}) == set()
        assert _already_published_platforms({"platform_publish_status": 123}) == set()


class TestRetryOnly:
    """R-83: retry-only mode (2nd daily run) recovers + retries failed platforms
    but must NEVER select a fresh VISUAL_READY blueprint (would double the day's
    content)."""

    @staticmethod
    def _queried_statuses(client) -> list[str]:
        out = []
        for call in client.get_blueprints_by_status.call_args_list:
            out.append(call.args[0] if call.args else call.kwargs.get("status"))
        return out

    def test_retry_only_never_selects_fresh_visual_ready(self):
        client = _make_client_mock([])
        enforcer = _make_cap_enforcer()
        exit_code = run_publish(
            niche_id="gaming",
            backlog_client=client,
            daily_cap=enforcer,
            enabled_platforms=["instagram"],
            retry_only=True,
        )
        queried = self._queried_statuses(client)
        assert "VISUAL_READY" not in queried  # the core safety property
        assert "PUBLISHED" in queried  # the retry pass still runs
        assert exit_code == EXIT_SUCCESS

    def test_normal_mode_selects_visual_ready_and_still_retries(self):
        # No fresh blueprints: normal mode queries VISUAL_READY AND (R-83 fix)
        # still runs the retry pass instead of returning early.
        client = _make_client_mock([])
        enforcer = _make_cap_enforcer()
        exit_code = run_publish(
            niche_id="gaming",
            backlog_client=client,
            daily_cap=enforcer,
            enabled_platforms=["instagram"],
            retry_only=False,
        )
        queried = self._queried_statuses(client)
        assert "VISUAL_READY" in queried
        assert "PUBLISHED" in queried  # retry pass runs even with no fresh content
        assert exit_code == EXIT_NO_BLUEPRINTS


# ---------------------------------------------------------------------------
# R-35: proactive token refresh on the publish path
# ---------------------------------------------------------------------------


class TestPreflightTokenRefresh:
    # Preflight tests patch the preflight module's bindings (see comment
    # on _PREFLIGHT_*_PATCH above) since refactor-#9 PR 4/N moved the
    # implementation off the orchestrator's namespace.

    @patch(_PREFLIGHT_CLIENT_PATCH)
    @patch(_PREFLIGHT_CRED_PATCH)
    def test_threads_token_refreshed_proactively(self, mock_creds, mock_get_client):
        """When Threads is enabled, refresh_token_if_needed() is called before
        publishing — the fix for a dark channel's 60-day token lapsing."""
        mock_creds.return_value = {"access_token": "t", "user_id": "u"}
        client = MagicMock()
        mock_get_client.return_value = client

        _preflight_token_refresh("gaming", ["instagram", "threads"])

        mock_get_client.assert_called_once_with("threads", access_token="t", user_id="u")
        client.refresh_token_if_needed.assert_called_once()

    @patch(_PREFLIGHT_CLIENT_PATCH)
    @patch(_PREFLIGHT_CRED_PATCH)
    def test_non_threads_platforms_are_skipped(self, mock_creds, mock_get_client):
        _preflight_token_refresh("gaming", ["instagram", "youtube", "facebook", "x"])
        mock_get_client.assert_not_called()

    @patch(_PREFLIGHT_CLIENT_PATCH)
    @patch(_PREFLIGHT_CRED_PATCH, return_value=None)
    def test_missing_threads_creds_is_non_fatal(self, mock_creds, mock_get_client):
        """No creds → no client built, no crash (logs a warning)."""
        _preflight_token_refresh("gaming", ["threads"])
        mock_get_client.assert_not_called()

    @patch(_PREFLIGHT_CLIENT_PATCH)
    @patch(_PREFLIGHT_CRED_PATCH)
    def test_refresh_failure_is_swallowed(self, mock_creds, mock_get_client):
        """A refresh that raises must NOT propagate — publishing proceeds."""
        mock_creds.return_value = {"access_token": "t", "user_id": "u"}
        client = MagicMock()
        client.refresh_token_if_needed.side_effect = RuntimeError("network down")
        mock_get_client.return_value = client

        # Must not raise.
        _preflight_token_refresh("gaming", ["threads"])

    @patch(_RECORD_PATCH)
    @patch(_CLIENT_PATCH)
    @patch(_PREFLIGHT_CRED_PATCH, return_value={})
    def test_run_publish_invokes_preflight(self, mock_creds, mock_get_client, mock_record):
        """run_publish runs the preflight (here: threads enabled →
        preflight.resolve_client_kwargs called for 'threads')."""
        client = _make_client_mock([])
        enforcer = _make_cap_enforcer()
        mock_get_client.side_effect = lambda pid, **kw: MagicMock()

        run_publish(
            niche_id="gaming",
            backlog_client=client,
            daily_cap=enforcer,
            enabled_platforms=["threads"],
        )
        # preflight's creds resolver was asked for threads.
        assert any(call.args and call.args[0] == "threads" for call in mock_creds.call_args_list)


class TestExitCodeContract:
    """Pin the exit-code semantics that genlab-publisher.service's
    SuccessExitStatus allowlist depends on. Drift here would silently
    re-introduce the 2026-06-16 alert-fatigue problem (3/4 non-zero
    exits being benign yet firing OnFailure handlers)."""

    def test_exit_code_constants_have_documented_values(self):
        """Drift detector — the unit file pins these exact codes."""
        from genlab_core.publishing.publish_all_platforms import (
            EXIT_ALL_FAILED,
            EXIT_DAILY_CAP,
            EXIT_LOCK_HELD,
            EXIT_NO_BLUEPRINTS,
            EXIT_SUCCESS,
            EXIT_UNEXPECTED,
        )

        # These values are pinned by genlab-publisher.service's
        # SuccessExitStatus=0 1 3 4 — changing them silently breaks
        # the dashboard alert signal.
        assert EXIT_SUCCESS == 0, "SUCCESS must be 0 (default 'success')"
        assert EXIT_NO_BLUEPRINTS == 1, "NO_BLUEPRINTS must be 1 (allowlisted as benign)"
        assert EXIT_ALL_FAILED == 2, "ALL_FAILED must be 2 (real signal, fires OnFailure)"
        assert EXIT_DAILY_CAP == 3, "DAILY_CAP must be 3 (allowlisted as benign)"
        assert EXIT_LOCK_HELD == 4, "LOCK_HELD must be 4 (allowlisted as benign)"
        assert EXIT_UNEXPECTED == 5, "UNEXPECTED must be 5 (real signal, fires OnFailure)"

    def test_systemd_unit_pins_correct_allowlist(self):
        """The .service file and the Python enum must agree."""
        from pathlib import Path

        # Find the unit file at the repo root
        repo_root = Path(__file__).resolve().parents[3]
        unit = repo_root / "deploy" / "systemd-phase2" / "genlab-publisher.service"
        if not unit.exists():
            import pytest

            pytest.skip(f"unit file not found at {unit}")
        text = unit.read_text()
        assert "SuccessExitStatus=0 1 3 4" in text, (
            "publisher.service SuccessExitStatus must allowlist exits "
            "0 (SUCCESS), 1 (NO_BLUEPRINTS), 3 (DAILY_CAP), 4 (LOCK_HELD). "
            "Codes 2 (ALL_FAILED) + 5 (UNEXPECTED) MUST NOT be allowlisted "
            "— they are the operator-actionable signals the L4 OnFailure "
            "handler exists to surface."
        )


# ── 2026-06-18: per-niche platform-enabled filter ──────────────


class TestFilterEnabledPlatforms:
    """Publisher must respect ``platforms.<name>.enabled: false`` in
    each niche's publishing.yaml. Pre-2026-06-18 the publisher
    hardcoded [instagram, youtube, facebook, twitter, threads]
    regardless of per-niche config. 3 niches (sports, movies, anime)
    set ``x.enabled: false`` (no Twitter creds); the publisher tried
    Twitter anyway → CREDENTIAL failure → ``any_success=False`` →
    ``EXIT_ALL_FAILED`` → systemd marked publisher.service as failed
    every cycle.
    """

    DEFAULT_PLATFORMS = ["instagram", "youtube", "facebook", "twitter", "threads"]

    def test_returns_default_when_no_config_file(self, tmp_path, monkeypatch):
        """If publishing.yaml doesn't exist for the niche, no filter
        applies — full default list passes through."""
        from genlab_core.publishing import publish_all_platforms

        # Patch NICHE_DIR_NAMES to point at a non-existent dir
        monkeypatch.setattr(
            "genlab_core.pipeline.cli.NICHE_DIR_NAMES",
            {"fake_niche": "FakeNiche"},
        )
        monkeypatch.setattr(
            "genlab_core.pipeline.cli._resolve_genlab_root",
            lambda: tmp_path,
        )

        result = publish_all_platforms._filter_enabled_platforms(
            "fake_niche", self.DEFAULT_PLATFORMS
        )
        assert result == self.DEFAULT_PLATFORMS

    def test_drops_explicitly_disabled_platform(self, tmp_path, monkeypatch):
        """``x.enabled: false`` removes 'twitter' from the list
        (with config→publisher name alias applied)."""
        from genlab_core.publishing import publish_all_platforms

        cfg_dir = tmp_path / "FakeNiche" / "config"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "publishing.yaml").write_text(
            "platforms:\n"
            "  instagram:\n    enabled: true\n"
            "  x:\n    enabled: false\n"
            "  youtube:\n    enabled: true\n"
        )
        monkeypatch.setattr(
            "genlab_core.pipeline.cli.NICHE_DIR_NAMES",
            {"fake_niche": "FakeNiche"},
        )
        monkeypatch.setattr(
            "genlab_core.pipeline.cli._resolve_genlab_root",
            lambda: tmp_path,
        )

        result = publish_all_platforms._filter_enabled_platforms(
            "fake_niche", self.DEFAULT_PLATFORMS
        )
        assert "twitter" not in result
        assert "instagram" in result
        assert "youtube" in result

    def test_x_twitter_alias_also_works(self, tmp_path, monkeypatch):
        """Some configs may use ``x_twitter`` instead of ``x`` —
        both alias to publisher-side ``twitter`` and get dropped."""
        from genlab_core.publishing import publish_all_platforms

        cfg_dir = tmp_path / "FakeNiche" / "config"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "publishing.yaml").write_text("platforms:\n  x_twitter:\n    enabled: false\n")
        monkeypatch.setattr(
            "genlab_core.pipeline.cli.NICHE_DIR_NAMES",
            {"fake_niche": "FakeNiche"},
        )
        monkeypatch.setattr(
            "genlab_core.pipeline.cli._resolve_genlab_root",
            lambda: tmp_path,
        )

        result = publish_all_platforms._filter_enabled_platforms(
            "fake_niche", self.DEFAULT_PLATFORMS
        )
        assert "twitter" not in result

    def test_no_platforms_block_returns_default(self, tmp_path, monkeypatch):
        """BB-style configs use per-platform yamls (no consolidated
        ``platforms`` block). Filter returns default unchanged so
        existing BB behaviour doesn't break."""
        from genlab_core.publishing import publish_all_platforms

        cfg_dir = tmp_path / "BBNiche" / "config"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "publishing.yaml").write_text(
            "auto_publish:\n  enabled: false\n# No platforms block — BB uses per-platform yamls\n"
        )
        monkeypatch.setattr(
            "genlab_core.pipeline.cli.NICHE_DIR_NAMES",
            {"bb_niche": "BBNiche"},
        )
        monkeypatch.setattr(
            "genlab_core.pipeline.cli._resolve_genlab_root",
            lambda: tmp_path,
        )

        result = publish_all_platforms._filter_enabled_platforms("bb_niche", self.DEFAULT_PLATFORMS)
        assert result == self.DEFAULT_PLATFORMS

    def test_enabled_true_does_not_filter(self, tmp_path, monkeypatch):
        """Only explicit ``enabled: false`` drops a platform.
        ``enabled: true`` or absent does nothing."""
        from genlab_core.publishing import publish_all_platforms

        cfg_dir = tmp_path / "FakeNiche" / "config"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "publishing.yaml").write_text(
            "platforms:\n  instagram:\n    enabled: true\n  twitter:\n    enabled: true\n"
        )
        monkeypatch.setattr(
            "genlab_core.pipeline.cli.NICHE_DIR_NAMES",
            {"fake_niche": "FakeNiche"},
        )
        monkeypatch.setattr(
            "genlab_core.pipeline.cli._resolve_genlab_root",
            lambda: tmp_path,
        )

        result = publish_all_platforms._filter_enabled_platforms(
            "fake_niche", self.DEFAULT_PLATFORMS
        )
        assert result == self.DEFAULT_PLATFORMS

    def test_malformed_yaml_returns_default_gracefully(self, tmp_path, monkeypatch):
        """Yaml parse error → log warning + return default. Don't
        crash the publisher because of a config bug."""
        from genlab_core.publishing import publish_all_platforms

        cfg_dir = tmp_path / "FakeNiche" / "config"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "publishing.yaml").write_text("not: valid: yaml: at all: ::: :::\n")
        monkeypatch.setattr(
            "genlab_core.pipeline.cli.NICHE_DIR_NAMES",
            {"fake_niche": "FakeNiche"},
        )
        monkeypatch.setattr(
            "genlab_core.pipeline.cli._resolve_genlab_root",
            lambda: tmp_path,
        )

        result = publish_all_platforms._filter_enabled_platforms(
            "fake_niche", self.DEFAULT_PLATFORMS
        )
        assert result == self.DEFAULT_PLATFORMS

    def test_gaming_nested_layout_works(self, tmp_path, monkeypatch):
        """Gaming uses CriticalRush/niches/gaming/config/publishing.yaml
        not the flat layout. Filter must find both."""
        from genlab_core.publishing import publish_all_platforms

        # Gaming-style nested layout
        cfg_dir = tmp_path / "CriticalRush" / "niches" / "gaming" / "config"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "publishing.yaml").write_text("platforms:\n  facebook:\n    enabled: false\n")
        monkeypatch.setattr(
            "genlab_core.pipeline.cli.NICHE_DIR_NAMES",
            {"gaming": "CriticalRush"},
        )
        monkeypatch.setattr(
            "genlab_core.pipeline.cli._resolve_genlab_root",
            lambda: tmp_path,
        )

        result = publish_all_platforms._filter_enabled_platforms("gaming", self.DEFAULT_PLATFORMS)
        assert "facebook" not in result

    # ── PR #514 (2026-06-24) — list-format allowlist support ──────────

    def test_list_format_allowlist_filters_to_named_platforms(self, tmp_path, monkeypatch):
        """``platforms.enabled: [list]`` form acts as an ALLOWLIST.

        Mirrors gaming's actual publishing.yaml shape:

            platforms:
              enabled:
                - instagram
                - youtube
                # - twitter        # Disabled Sprint 30
                - facebook

        Pre-PR-#514 the filter saw ``enabled`` whose VALUE was a list
        (not a dict), failed ``isinstance(settings, dict)``, skipped
        every entry, and returned the FULL default list — including
        twitter, which then hit a CREDENTIAL skip every cycle.
        """
        from genlab_core.publishing import publish_all_platforms

        cfg_dir = tmp_path / "FakeNiche" / "config"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "publishing.yaml").write_text(
            "platforms:\n  enabled:\n    - instagram\n    - youtube\n    - facebook\n"
        )
        monkeypatch.setattr(
            "genlab_core.pipeline.cli.NICHE_DIR_NAMES",
            {"fake_niche": "FakeNiche"},
        )
        monkeypatch.setattr(
            "genlab_core.pipeline.cli._resolve_genlab_root",
            lambda: tmp_path,
        )

        result = publish_all_platforms._filter_enabled_platforms(
            "fake_niche", self.DEFAULT_PLATFORMS
        )
        # ALLOWLIST: only the 3 listed platforms (twitter + threads dropped)
        assert "instagram" in result
        assert "youtube" in result
        assert "facebook" in result
        assert "twitter" not in result
        assert "threads" not in result

    def test_list_format_x_twitter_alias_in_allowlist(self, tmp_path, monkeypatch):
        """When the list uses ``x_twitter`` or ``x`` aliases, they map
        to publisher-side ``twitter`` and stay enabled."""
        from genlab_core.publishing import publish_all_platforms

        cfg_dir = tmp_path / "FakeNiche" / "config"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "publishing.yaml").write_text(
            "platforms:\n  enabled:\n    - instagram\n    - x_twitter\n"
        )
        monkeypatch.setattr(
            "genlab_core.pipeline.cli.NICHE_DIR_NAMES",
            {"fake_niche": "FakeNiche"},
        )
        monkeypatch.setattr(
            "genlab_core.pipeline.cli._resolve_genlab_root",
            lambda: tmp_path,
        )

        result = publish_all_platforms._filter_enabled_platforms(
            "fake_niche", self.DEFAULT_PLATFORMS
        )
        assert "twitter" in result, (
            "x_twitter alias in list-format allowlist must keep twitter enabled"
        )
        assert "instagram" in result
        assert "youtube" not in result
        assert "facebook" not in result

    def test_list_format_empty_allowlist_filters_everything(self, tmp_path, monkeypatch):
        """Edge case: empty list means NO platforms enabled.

        Defensive — this is unlikely in practice but the filter must
        not crash. Returns empty list; caller's run_publish would then
        exit early with no work to do.
        """
        from genlab_core.publishing import publish_all_platforms

        cfg_dir = tmp_path / "FakeNiche" / "config"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "publishing.yaml").write_text("platforms:\n  enabled: []\n")
        monkeypatch.setattr(
            "genlab_core.pipeline.cli.NICHE_DIR_NAMES",
            {"fake_niche": "FakeNiche"},
        )
        monkeypatch.setattr(
            "genlab_core.pipeline.cli._resolve_genlab_root",
            lambda: tmp_path,
        )

        result = publish_all_platforms._filter_enabled_platforms(
            "fake_niche", self.DEFAULT_PLATFORMS
        )
        assert result == []

    def test_list_format_wins_when_both_shapes_present(self, tmp_path, monkeypatch):
        """If both list-allowlist AND per-platform dicts exist, the
        list wins (more explicit operator intent). Documents the
        priority order from PR #514's docstring."""
        from genlab_core.publishing import publish_all_platforms

        cfg_dir = tmp_path / "FakeNiche" / "config"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "publishing.yaml").write_text(
            "platforms:\n"
            "  enabled:\n"
            "    - instagram\n"  # allowlist says only IG
            "  facebook:\n"
            "    enabled: true\n"  # but FB has a dict saying true — list wins
        )
        monkeypatch.setattr(
            "genlab_core.pipeline.cli.NICHE_DIR_NAMES",
            {"fake_niche": "FakeNiche"},
        )
        monkeypatch.setattr(
            "genlab_core.pipeline.cli._resolve_genlab_root",
            lambda: tmp_path,
        )

        result = publish_all_platforms._filter_enabled_platforms(
            "fake_niche", self.DEFAULT_PLATFORMS
        )
        # List-allowlist wins: only instagram passes
        assert result == ["instagram"]
        assert "instagram" in result
