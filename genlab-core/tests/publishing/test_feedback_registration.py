"""Tests for :mod:`genlab_core.publishing.feedback_registration`.

Lives next to its module home (paralleling the PR 6d/N extraction in
the publish_all_platforms decomposition).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from genlab_core.publishing.feedback_registration import (
    _build_bandit_context,
    _is_published_status,
    register_pending_feedback,
)
from genlab_core.publishing.parallel_publish import ParallelPublishOutcome

# ---------------------------------------------------------------------------
# _is_published_status — accepts both bare-string and dict shapes
# ---------------------------------------------------------------------------


class TestIsPublishedStatus:
    def test_bare_string(self) -> None:
        assert _is_published_status("PUBLISHED") is True

    def test_dict_with_published(self) -> None:
        assert _is_published_status({"status": "PUBLISHED"}) is True

    def test_dict_with_failed(self) -> None:
        assert _is_published_status({"status": "FAILED", "attempts": 2}) is False

    def test_bare_failed_string(self) -> None:
        assert _is_published_status("FAILED") is False

    def test_none(self) -> None:
        assert _is_published_status(None) is False


# ---------------------------------------------------------------------------
# register_pending_feedback — integration through the loop
# ---------------------------------------------------------------------------


def _outcome(
    platforms_with_status: dict[str, str | dict], post_ids: dict[str, str]
) -> ParallelPublishOutcome:
    return ParallelPublishOutcome(
        platform_status=platforms_with_status,
        any_success=any(_is_published_status(v) for v in platforms_with_status.values()),
        successful_post_ids=post_ids,
    )


class TestRegisterPendingFeedback:
    @patch("genlab_core.learning.pending_feedback_task.PendingFeedbackTask")
    @patch("genlab_core.learning.pending_feedback_store.PendingFeedbackStore")
    def test_one_task_per_published_platform(self, MockStore, MockTask) -> None:
        outcome = _outcome(
            {"instagram": "PUBLISHED", "youtube": "PUBLISHED", "facebook": {"status": "FAILED"}},
            {"instagram": "ig123", "youtube": "yt456"},
        )
        register_pending_feedback(
            outcome=outcome,
            fields={"hook": "h"},
            record_id="rec",
            candidate_id="cand",
            niche_id="gaming",
            backlog_client=MagicMock(),
        )
        # 2 successes → 2 task creations.
        assert MockTask.call_count == 2

    @patch("genlab_core.learning.pending_feedback_task.PendingFeedbackTask")
    @patch("genlab_core.learning.pending_feedback_store.PendingFeedbackStore")
    def test_post_id_passed_from_outcome(self, MockStore, MockTask) -> None:
        """The post_id MUST come from outcome.successful_post_ids — the
        extraction's whole point is to stop re-iterating futures."""
        outcome = _outcome(
            {"instagram": "PUBLISHED"},
            {"instagram": "ig999"},
        )
        register_pending_feedback(
            outcome=outcome,
            fields={"hook": "h"},
            record_id="rec",
            candidate_id="cand",
            niche_id="gaming",
            backlog_client=MagicMock(),
        )
        MockTask.assert_called_once()
        assert MockTask.call_args.kwargs["platform_post_id"] == "ig999"

    @patch("genlab_core.learning.pending_feedback_task.PendingFeedbackTask")
    @patch("genlab_core.learning.pending_feedback_store.PendingFeedbackStore")
    def test_missing_post_id_falls_back_to_empty(self, MockStore, MockTask) -> None:
        """For platforms in ``prior_published`` the post_id was lost across
        the run boundary; the field falls back to empty string rather than
        crashing on KeyError."""
        outcome = _outcome({"instagram": "PUBLISHED"}, post_ids={})  # no post_id
        register_pending_feedback(
            outcome=outcome,
            fields={"hook": "h"},
            record_id="rec",
            candidate_id="cand",
            niche_id="gaming",
            backlog_client=MagicMock(),
        )
        assert MockTask.call_args.kwargs["platform_post_id"] == ""

    @patch("genlab_core.learning.pending_feedback_task.PendingFeedbackTask")
    @patch("genlab_core.learning.pending_feedback_store.PendingFeedbackStore")
    def test_store_failure_non_fatal(self, MockStore, MockTask) -> None:
        """A store/network error MUST NOT propagate — the publish has already
        succeeded; we just lose the learning signal for this post."""
        MockStore.return_value.create.side_effect = RuntimeError("graph down")
        outcome = _outcome({"instagram": "PUBLISHED"}, {"instagram": "ig1"})
        # Must not raise.
        register_pending_feedback(
            outcome=outcome,
            fields={"hook": "h"},
            record_id="rec",
            candidate_id="cand",
            niche_id="gaming",
            backlog_client=MagicMock(),
        )


# ---------------------------------------------------------------------------
# _build_bandit_context — graceful fallback when learning imports break
# ---------------------------------------------------------------------------


class TestBuildBanditContext:
    @patch("genlab_core.learning.linucb.build_content_context")
    @patch("genlab_core.learning.hook_features.build_feature_vector")
    def test_builds_full_context_with_features_and_linucb(self, mock_features, mock_linucb) -> None:
        mock_features.return_value = {"length": 50}
        mock_linucb.return_value = MagicMock(tolist=lambda: [1, 2, 3, 4, 5, 6])
        ctx = _build_bandit_context({"hook": "Some hook text"}, "gaming")
        assert ctx is not None
        assert "hook_features" in ctx
        assert "linucb_context" in ctx
        assert ctx["linucb_context"] == [1, 2, 3, 4, 5, 6]
        assert "extra_arms" not in ctx  # no hook_style → no extra arm

    @patch("genlab_core.learning.linucb.build_content_context")
    @patch("genlab_core.learning.hook_features.build_feature_vector")
    def test_hook_style_emits_extra_arm(self, mock_features, mock_linucb) -> None:
        """The hook_style arm is what closes the LinUCB → style-arm loop
        opened in commit 84b7801 (R-Break-11 fix)."""
        mock_features.return_value = {}
        mock_linucb.return_value = MagicMock(tolist=lambda: [0] * 6)
        ctx = _build_bandit_context({"hook": "h", "hook_style": "shock"}, "gaming")
        assert ctx["extra_arms"] == ["style:gaming:shock"]

    @patch("genlab_core.learning.hook_features.build_feature_vector")
    def test_returns_none_on_import_error(self, mock_features) -> None:
        """An unavailable learning-loop dep returns ``None`` — PendingFeedback
        registration still proceeds, just with bandit_context=None."""
        mock_features.side_effect = ImportError("xgboost missing")
        ctx = _build_bandit_context({"hook": "h"}, "gaming")
        assert ctx is None
