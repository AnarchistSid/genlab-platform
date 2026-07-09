"""Tests for PendingFeedbackTask model."""

from datetime import UTC, datetime

from genlab_core.learning.pending_feedback_task import (
    PendingFeedbackTask,
)


class TestPendingFeedbackTask:
    def test_created_with_correct_fields(self):
        task = PendingFeedbackTask(
            content_id="story_123",
            platform="youtube",
            niche_id="gaming",
            published_at=datetime(2026, 3, 7, 12, 0, tzinfo=UTC),
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
            published_at=datetime.now(tz=UTC),
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
                published_at=datetime.now(tz=UTC),
                platform_post_id="yt_1",
                collection_status=status,
            )
            assert task.is_complete is True

        for status in ("awaiting_6h", "awaiting_24h", "awaiting_48h", "awaiting_168h"):
            task = PendingFeedbackTask(
                content_id="s1",
                platform="youtube",
                published_at=datetime.now(tz=UTC),
                platform_post_id="yt_1",
                collection_status=status,
            )
            assert task.is_complete is False

    def test_bandit_arm_null_handled_gracefully(self):
        """Compose fix not yet applied — bandit_arm and context are null."""
        task = PendingFeedbackTask(
            content_id="s1",
            platform="youtube",
            published_at=datetime.now(tz=UTC),
            platform_post_id="yt_1",
            bandit_arm=None,
            bandit_context=None,
        )
        assert task.bandit_arm is None
        assert task.bandit_context is None
        # Should not raise on serialisation
        fields = task.to_sharepoint_fields()
        assert fields["arm_id"] == ""
        assert fields["bandit_context"] == ""

    def test_serialises_to_sharepoint_fields(self):
        task = PendingFeedbackTask(
            content_id="s1",
            platform="instagram",
            published_at=datetime(2026, 3, 7, 10, 0, tzinfo=UTC),
            platform_post_id="ig_123",
            content_type="viral_moment",
            hook_type="You won't believe this play",
            bandit_arm="viral_moment__instagram",
            bandit_context={"score": 0.8},
        )
        fields = task.to_sharepoint_fields()

        assert fields["Title"] == "instagram__ig_123"
        assert fields["platform"] == "instagram"
        # post_id is stored as platform:native so it joins analytics.post_id
        assert fields["post_id"] == "instagram:ig_123"
        assert fields["content_type"] == "viral_moment"
        assert fields["hook_type"] == "You won't believe this play"
        assert fields["collection_status"] == "awaiting_6h"
        assert fields["arm_id"] == "viral_moment__instagram"
        assert '"score": 0.8' in fields["bandit_context"]
        assert fields["niche_id"] == "gaming"  # default

    def test_post_id_prefixed_per_platform(self):
        """post_id is stored as platform:native for analytics joinability."""
        task = PendingFeedbackTask(
            content_id="s1",
            platform="youtube",
            published_at=datetime(2026, 3, 7, 10, 0, tzinfo=UTC),
            platform_post_id="abc123",
        )
        assert task.to_sharepoint_fields()["post_id"] == "youtube:abc123"

    def test_post_id_empty_stays_empty(self):
        """A missing native id must not produce a dangling 'platform:' prefix."""
        task = PendingFeedbackTask(
            content_id="s1",
            platform="youtube",
            published_at=datetime(2026, 3, 7, 10, 0, tzinfo=UTC),
            platform_post_id="",
        )
        assert task.to_sharepoint_fields()["post_id"] == ""

    def test_hook_fields_serialised(self):
        task = PendingFeedbackTask(
            content_id="s1",
            platform="instagram",
            published_at=datetime(2026, 3, 7, 10, 0, tzinfo=UTC),
            platform_post_id="ig_456",
            hook_text="You won't believe this AI breakthrough",
            hook_type="reaction",
        )
        fields = task.to_sharepoint_fields()
        assert fields["hook_text"] == "You won't believe this AI breakthrough"
        assert fields["hook_length"] == 38  # character count

    def test_hook_fields_omitted_when_empty(self):
        task = PendingFeedbackTask(
            content_id="s1",
            platform="instagram",
            published_at=datetime(2026, 3, 7, 10, 0, tzinfo=UTC),
            platform_post_id="ig_789",
        )
        fields = task.to_sharepoint_fields()
        assert "hook_text" not in fields

    def test_sharepoint_id_field(self):
        task = PendingFeedbackTask(
            content_id="s1",
            platform="youtube",
            published_at=datetime.now(tz=UTC),
            platform_post_id="yt_1",
            sharepoint_id="sp_abc123",
        )
        assert task.sharepoint_id == "sp_abc123"


# ── task #623 (2026-07-09): post_id double-prefix bug ────────────────
#
# 297 pending_feedback rows have shape ``facebook:facebook:1181...``
# because the pre-fix ``to_sharepoint_fields`` unconditionally prefixed
# ``self.platform_post_id`` — which had ALREADY been prefixed upstream
# by ``feedback_registration._normalize_post_id``. The double-prefix
# then broke ``metric_collector.fetch_platform_metrics``'s single
# ``.split(":", 1)`` strip, sending malformed IDs to platform APIs.
#
# Root cause of the entire learning stack producing reward=0 for weeks.


def test_to_sharepoint_fields_prefixes_raw_post_id():
    """Bare ID (no prefix) gets ``{platform}:`` added — same as pre-fix
    behavior for that path (backward compat)."""
    from datetime import UTC, datetime

    from genlab_core.learning.pending_feedback_task import PendingFeedbackTask

    task = PendingFeedbackTask(
        content_id="c1",
        platform="facebook",
        niche_id="ai_creators",
        published_at=datetime.now(tz=UTC),
        platform_post_id="1181921760782769",
    )
    fields = task.to_sharepoint_fields()
    assert fields["post_id"] == "facebook:1181921760782769", (
        "Bare ID must get ONE platform prefix added — that's the "
        "backward-compat path for callers that pass raw IDs."
    )


def test_to_sharepoint_fields_does_not_double_prefix_already_prefixed():
    """THE CORE #623 FIX: already-prefixed ID stays as single prefix,
    not double. This is the exact 2026-07-09 bug that produced 297
    rows with shape ``facebook:facebook:1181...``."""
    from datetime import UTC, datetime

    from genlab_core.learning.pending_feedback_task import PendingFeedbackTask

    task = PendingFeedbackTask(
        content_id="c1",
        platform="facebook",
        niche_id="ai_creators",
        published_at=datetime.now(tz=UTC),
        # Simulates upstream _normalize_post_id having already prefixed
        platform_post_id="facebook:1181921760782769",
    )
    fields = task.to_sharepoint_fields()
    assert fields["post_id"] == "facebook:1181921760782769", (
        f"Double prefix bug regressed. Got {fields['post_id']!r}. "
        "The stored post_id must be single-prefixed even when the "
        "input platform_post_id was already prefixed."
    )


def test_to_sharepoint_fields_empty_post_id_stays_empty():
    """Empty ID → empty output (no spurious `facebook:` alone)."""
    from datetime import UTC, datetime

    from genlab_core.learning.pending_feedback_task import PendingFeedbackTask

    task = PendingFeedbackTask(
        content_id="c1",
        platform="facebook",
        niche_id="ai_creators",
        published_at=datetime.now(tz=UTC),
        platform_post_id="",
    )
    fields = task.to_sharepoint_fields()
    assert fields["post_id"] == "", (
        "Empty post_id must pass through as empty — don't produce "
        "'facebook:' with nothing after it."
    )


def test_helper_idempotent_prefix_never_triples():
    """The helper must never triple-prefix. If a future bug adds a
    third prefix upstream (e.g. ``facebook:facebook:1234``), the
    helper still returns as-is — no growth."""
    from genlab_core.learning.pending_feedback_task import (
        _post_id_with_platform_prefix,
    )

    assert _post_id_with_platform_prefix("facebook", "facebook:1234") == "facebook:1234"
    # Triple-prefixed input — helper preserves as-is; write side stays
    # idempotent even if upstream regressed further.
    assert (
        _post_id_with_platform_prefix("facebook", "facebook:facebook:1234")
        == "facebook:facebook:1234"
    ), (
        "Helper only PREVENTS growth; it doesn't SHRINK. The strip "
        "loop in metric_collector handles historical bad data. "
        "Growing the helper's responsibility could hide upstream bugs."
    )


def test_helper_leaves_foreign_prefix_alone():
    """A caller who intentionally tagged ``other:id`` isn't overridden.
    Matches ``feedback_registration._normalize_post_id`` semantics —
    ``if ":" in post_id: return post_id``."""
    from genlab_core.learning.pending_feedback_task import (
        _post_id_with_platform_prefix,
    )

    # Called from facebook context but ID is tagged for another platform
    # — trust the caller's intent.
    assert _post_id_with_platform_prefix("facebook", "youtube:abc") == "youtube:abc"
