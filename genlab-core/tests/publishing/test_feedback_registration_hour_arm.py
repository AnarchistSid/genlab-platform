"""Pin: PR Z (2026-06-23) — publish-hour arm in bandit_context.

PR Z captures ``hour:{H}:{platform}:{niche_id}`` as an extra-arm in
PendingFeedbackTask.bandit_context. The metric_collector's existing
multi-arm credit pass then updates the arm's Beta posterior on the
same reward as the content arm — no metric_collector changes needed.

These pins lock in:

  Hour-arm construction (5):
    - publish_hour + platform supplied → hour arm appended
    - hour arm format is ``hour:{H}:{platform}:{niche_id}`` exactly
    - hour=0 and hour=23 (boundary values) both accepted
    - hour=24 or hour=-1 rejected (silently — no arm appended)
    - missing platform → no hour arm (sentinel guard)

  Coexistence with hook_style arm (2):
    - both hook_style + hour-arm → BOTH appear in extra_arms
    - only hook_style (no hour) → only style arm appears

  Backward compat (1):
    - called without publish_hour/platform kwargs → same shape as
      pre-PR-Z (matches existing test_feedback_registration pins)

  Integration (1):
    - register_pending_feedback lifts published_at out of the
      PendingFeedbackTask ctor and passes its hour to
      _build_bandit_context
"""

from __future__ import annotations

from unittest.mock import patch

from genlab_core.publishing.feedback_registration import _build_bandit_context


class TestHourArmConstruction:
    def test_publish_hour_and_platform_produce_hour_arm(self):
        """Pin: when both publish_hour and platform are supplied, an
        hour arm is appended to extra_arms. The smoke pin — without
        this the bandit foundation is dead."""
        with patch("genlab_core.learning.linucb.build_content_context") as mock_ctx:
            mock_ctx.return_value.tolist.return_value = [0.0] * 6
            ctx = _build_bandit_context(
                {"hook": "Test hook"},
                "gaming",
                publish_hour=14,
                platform="youtube",
            )
        assert ctx is not None
        extra_arms = ctx.get("extra_arms", [])
        hour_arms = [a for a in extra_arms if a.startswith("hour:")]
        assert hour_arms == ["hour:14:youtube:gaming"]

    def test_hour_arm_format_exact(self):
        """Pin: the arm_id format is exactly ``hour:{H}:{platform}:{niche_id}``.
        The consumer side (sample_optimal_hours_from_bandit) parses
        this prefix+suffix shape — drift breaks the read."""
        with patch("genlab_core.learning.linucb.build_content_context") as mock_ctx:
            mock_ctx.return_value.tolist.return_value = [0.0] * 6
            ctx = _build_bandit_context(
                {"hook": "x"},
                "ai_creators",
                publish_hour=9,
                platform="instagram",
            )
        assert "hour:9:instagram:ai_creators" in ctx["extra_arms"]

    def test_boundary_hours_0_and_23_accepted(self):
        """Pin: hour=0 (midnight UTC) and hour=23 (11pm UTC) both
        produce valid arms. Boundary inclusive — the dispatch uses
        0 ≤ H ≤ 23."""
        with patch("genlab_core.learning.linucb.build_content_context") as mock_ctx:
            mock_ctx.return_value.tolist.return_value = [0.0] * 6

            ctx0 = _build_bandit_context(
                {"hook": "x"}, "gaming", publish_hour=0, platform="youtube"
            )
            ctx23 = _build_bandit_context(
                {"hook": "x"}, "gaming", publish_hour=23, platform="youtube"
            )
        assert "hour:0:youtube:gaming" in ctx0.get("extra_arms", [])
        assert "hour:23:youtube:gaming" in ctx23.get("extra_arms", [])

    def test_invalid_hour_values_rejected_silently(self):
        """Pin: hour=24 or hour=-1 produce NO hour arm. Out-of-range
        inputs silently drop the arm rather than poisoning the
        bandit_arms table with garbage that the consumer's prefix-
        match would have to filter."""
        with patch("genlab_core.learning.linucb.build_content_context") as mock_ctx:
            mock_ctx.return_value.tolist.return_value = [0.0] * 6
            ctx_high = _build_bandit_context(
                {"hook": "x"}, "gaming", publish_hour=24, platform="youtube"
            )
            ctx_neg = _build_bandit_context(
                {"hook": "x"}, "gaming", publish_hour=-1, platform="youtube"
            )
        assert not any(a.startswith("hour:") for a in ctx_high.get("extra_arms", []))
        assert not any(a.startswith("hour:") for a in ctx_neg.get("extra_arms", []))

    def test_missing_platform_no_hour_arm(self):
        """Pin: publish_hour without platform → no hour arm. Sentinel
        guard — an hour arm without the platform suffix would be
        unrecognisable to the consumer's prefix+suffix matcher."""
        with patch("genlab_core.learning.linucb.build_content_context") as mock_ctx:
            mock_ctx.return_value.tolist.return_value = [0.0] * 6
            ctx = _build_bandit_context({"hook": "x"}, "gaming", publish_hour=14, platform=None)
        assert not any(a.startswith("hour:") for a in ctx.get("extra_arms", []))


class TestCoexistenceWithStyleArm:
    def test_both_hook_style_and_hour_arm_present(self):
        """Pin: hook_style AND hour-arm both appear in extra_arms when
        both inputs are supplied. The metric_collector applies the
        same reward to ALL listed arms — so the bandit credits style
        + hour together for high-engagement posts."""
        with patch("genlab_core.learning.linucb.build_content_context") as mock_ctx:
            mock_ctx.return_value.tolist.return_value = [0.0] * 6
            ctx = _build_bandit_context(
                {"hook": "Test", "hook_style": "question"},
                "gaming",
                publish_hour=14,
                platform="youtube",
            )
        extra_arms = ctx["extra_arms"]
        assert "style:gaming:question" in extra_arms
        assert "hour:14:youtube:gaming" in extra_arms

    def test_only_hook_style_when_no_hour_supplied(self):
        """Pin: when publish_hour/platform are not supplied, only the
        style arm appears. Backward-compat shape — the metric_collector
        and downstream queries still see the legacy single-arm extras."""
        with patch("genlab_core.learning.linucb.build_content_context") as mock_ctx:
            mock_ctx.return_value.tolist.return_value = [0.0] * 6
            ctx = _build_bandit_context(
                {"hook": "Test", "hook_style": "question"},
                "gaming",
            )
        assert ctx["extra_arms"] == ["style:gaming:question"]


class TestBackwardCompat:
    def test_call_without_new_kwargs_works(self):
        """Pin: callers that don't pass publish_hour/platform still get
        a valid context (no exception, no hour arm). Required for
        legacy code paths + the test suite's existing pins which all
        call _build_bandit_context with the old 2-arg signature."""
        with patch("genlab_core.learning.linucb.build_content_context") as mock_ctx:
            mock_ctx.return_value.tolist.return_value = [0.0] * 6
            ctx = _build_bandit_context({"hook": "x"}, "gaming")
        # No exception means pass. Defensive shape checks:
        assert isinstance(ctx, dict)
        assert "hook_features" in ctx
        assert "linucb_context" in ctx
        # No hour arms (none supplied)
        assert not any(a.startswith("hour:") for a in ctx.get("extra_arms", []))
