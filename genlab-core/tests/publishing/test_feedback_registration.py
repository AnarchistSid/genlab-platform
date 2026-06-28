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
        # W3.2 (2026-06-17): post_id is now normalized to ``{platform}:{id}``
        # so the join with publishing_analytics.post_id matches. Bare
        # 'ig999' becomes 'instagram:ig999'.
        assert MockTask.call_args.kwargs["platform_post_id"] == "instagram:ig999"

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

    # PR U (2026-06-28): IPS propensity wire-through pins. PR #634
    # shipped the storage column + the LinUCBBandit.select_with_
    # propensity producer; this PR closes the gap between
    # push_to_backlog._classify_arm_with_propensity and the
    # PendingFeedbackTask construction so the column actually gets
    # populated under the LinUCB path.

    @patch("genlab_core.learning.pending_feedback_task.PendingFeedbackTask")
    @patch("genlab_core.learning.pending_feedback_store.PendingFeedbackStore")
    def test_propensity_in_fields_flows_to_pending_feedback_task(self, MockStore, MockTask) -> None:
        """Pin: ``fields["bandit_propensity"]`` flows to
        ``PendingFeedbackTask.propensity`` AND a paired
        ``temperature=0.5`` lands on the task. The 0.5 matches
        ``linucb_picker._DETERMINISTIC_TEMPERATURE`` (and
        ``LinUCBBandit.DEFAULT_TEMPERATURE``) so IPS replay reads both
        producers under one convention."""
        outcome = _outcome({"instagram": "PUBLISHED"}, {"instagram": "ig1"})
        register_pending_feedback(
            outcome=outcome,
            fields={"hook": "h", "bandit_propensity": 0.6},
            record_id="rec",
            candidate_id="cand",
            niche_id="gaming",
            backlog_client=MagicMock(),
        )
        MockTask.assert_called_once()
        assert MockTask.call_args.kwargs["propensity"] == 0.6
        # Temperature is auto-paired with the propensity — the
        # producer-side (linucb_picker) uses 0.5 as the deterministic
        # temperature so this mirror keeps both ends consistent.
        assert MockTask.call_args.kwargs["temperature"] == 0.5

    @patch("genlab_core.learning.pending_feedback_task.PendingFeedbackTask")
    @patch("genlab_core.learning.pending_feedback_store.PendingFeedbackStore")
    def test_no_propensity_in_fields_keeps_task_propensity_none(self, MockStore, MockTask) -> None:
        """Pin: when fields lacks the ``bandit_propensity`` key (every
        non-LinUCB path — Thompson, force-explore, active-experiment,
        random-control, no-match default), both propensity AND
        temperature land as None on the task. Preserves the
        ``NULL = not applicable`` sentinel that downstream IPS replay
        uses to exclude rows."""
        outcome = _outcome({"instagram": "PUBLISHED"}, {"instagram": "ig1"})
        register_pending_feedback(
            outcome=outcome,
            fields={"hook": "h"},  # no bandit_propensity key
            record_id="rec",
            candidate_id="cand",
            niche_id="gaming",
            backlog_client=MagicMock(),
        )
        MockTask.assert_called_once()
        assert MockTask.call_args.kwargs["propensity"] is None
        # Pinned to None (NOT 0.5) so the task carries an unambiguous
        # "not applicable" signal — temperature only makes sense when
        # paired with a real propensity.
        assert MockTask.call_args.kwargs["temperature"] is None

    @patch("genlab_core.learning.pending_feedback_task.PendingFeedbackTask")
    @patch("genlab_core.learning.pending_feedback_store.PendingFeedbackStore")
    def test_propensity_value_passes_through_unchanged(self, MockStore, MockTask) -> None:
        """Pin: the propensity value is NOT rounded, clamped, or
        re-scaled at the registration layer. The picker already
        floored at ``_MIN_PROPENSITY``; this layer is a pure
        pass-through. Future refactors that touch this code path must
        preserve the exact float."""
        outcome = _outcome({"instagram": "PUBLISHED"}, {"instagram": "ig1"})
        register_pending_feedback(
            outcome=outcome,
            fields={"hook": "h", "bandit_propensity": 0.123},
            record_id="rec",
            candidate_id="cand",
            niche_id="gaming",
            backlog_client=MagicMock(),
        )
        assert MockTask.call_args.kwargs["propensity"] == 0.123


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
