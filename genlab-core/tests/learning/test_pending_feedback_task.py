"""Tests for PendingFeedbackTask model."""

from datetime import datetime, timezone


from genlab_core.learning.pending_feedback_task import (
    PendingFeedbackTask,
)


class TestPendingFeedbackTask:
    def test_created_with_correct_fields(self):
        task = PendingFeedbackTask(
            content_id="story_123",
            platform="youtube",
            niche_id="gaming",
            published_at=datetime(2026, 3, 7, 12, 0, tzinfo=timezone.utc),
            platform_post_id="vid_abc",
            content_type="game_launch_hype",
            hook_type="What just happened to Elden Ring?",
            bandit_arm="game_launch_hype__youtube",
            bandit_context={"hour": 12, "day_of_week": 5},
        )
        assert task.content_id == "story_123"
        assert task.platform == "youtube"
        assert task.platform_post_id == "vid_abc"
        assert task.bandit_arm == "game_launch_hype__youtube"
        assert task.collection_status == "awaiting_6h"
        assert not task.early_stop

    def test_pending_windows_excludes_completed(self):
        task = PendingFeedbackTask(
            content_id="s1",
            platform="instagram",
            published_at=datetime.now(tz=timezone.utc),
            platform_post_id="ig_1",
            completed_windows=["6h", "24h"],
        )
        assert task.pending_windows == ["48h", "168h"]
        assert "6h" not in task.pending_windows

    def test_is_complete_for_terminal_statuses(self):
        for status in ("complete", "error", "early_stopped"):
            task = PendingFeedbackTask(
                content_id="s1",
                platform="youtube",
                published_at=datetime.now(tz=timezone.utc),
                platform_post_id="yt_1",
                collection_status=status,
            )
            assert task.is_complete is True

        for status in ("awaiting_6h", "awaiting_24h", "awaiting_48h", "awaiting_168h"):
            task = PendingFeedbackTask(
                content_id="s1",
                platform="youtube",
                published_at=datetime.now(tz=timezone.utc),
                platform_post_id="yt_1",
                collection_status=status,
            )
            assert task.is_complete is False

    def test_bandit_arm_null_handled_gracefully(self):
        """Compose fix not yet applied — bandit_arm and context are null."""
        task = PendingFeedbackTask(
            content_id="s1",
            platform="youtube",
            published_at=datetime.now(tz=timezone.utc),
            platform_post_id="yt_1",
            bandit_arm=None,
            bandit_context=None,
        )
        assert task.bandit_arm is None
        assert task.bandit_context is None
        # Should not raise on serialisation
        fields = task.to_sharepoint_fields()
        assert fields["BanditArm"] == ""
        assert fields["BanditContext"] == ""

    def test_serialises_to_sharepoint_fields(self):
        task = PendingFeedbackTask(
            content_id="s1",
            platform="instagram",
            published_at=datetime(2026, 3, 7, 10, 0, tzinfo=timezone.utc),
            platform_post_id="ig_123",
            content_type="viral_moment",
            hook_type="You won't believe this play",
            bandit_arm="viral_moment__instagram",
            bandit_context={"score": 0.8},
        )
        fields = task.to_sharepoint_fields()

        assert fields["Title"] == "instagram__ig_123"
        assert fields["Platform"] == "instagram"
        assert fields["PostID"] == "ig_123"
        assert fields["PostContentType"] == "viral_moment"
        assert fields["HookType"] == "You won't believe this play"
        assert fields["Status"] == "awaiting_6h"
        assert fields["BanditArm"] == "viral_moment__instagram"
        assert '"score": 0.8' in fields["BanditContext"]
        assert fields["NicheId"] == "gaming"  # default

    def test_hook_fields_serialised(self):
        task = PendingFeedbackTask(
            content_id="s1",
            platform="instagram",
            published_at=datetime(2026, 3, 7, 10, 0, tzinfo=timezone.utc),
            platform_post_id="ig_456",
            hook_text="You won't believe this AI breakthrough",
            hook_type="reaction",
        )
        fields = task.to_sharepoint_fields()
        assert fields["HookText"] == "You won't believe this AI breakthrough"
        assert fields["HookLength"] == 38  # character count

    def test_hook_fields_omitted_when_empty(self):
        task = PendingFeedbackTask(
            content_id="s1",
            platform="instagram",
            published_at=datetime(2026, 3, 7, 10, 0, tzinfo=timezone.utc),
            platform_post_id="ig_789",
        )
        fields = task.to_sharepoint_fields()
        assert "HookText" not in fields

    def test_sharepoint_id_field(self):
        task = PendingFeedbackTask(
            content_id="s1",
            platform="youtube",
            published_at=datetime.now(tz=timezone.utc),
            platform_post_id="yt_1",
            sharepoint_id="sp_abc123",
        )
        assert task.sharepoint_id == "sp_abc123"
