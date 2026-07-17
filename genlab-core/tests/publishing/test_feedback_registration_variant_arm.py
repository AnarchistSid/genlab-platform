"""Pin: Layer 3 S5-prep — variant arm attribution in bandit_context.

When feedback_registration builds the bandit_context for a published
blueprint, ``variant:{niche_id}:{variant_type}`` is appended to
``extra_arms`` so downstream reward flow attributes credit to the
structural variant dimension alongside content_type + style + hour.

## Why this ships before S5

S5 bandit extension (variant-driven sampling) is blocked on real
observation data. But observations start accumulating from the moment
a variant blueprint publishes — IF the reward attribution wire
exists. Shipping this now means tomorrow's fires start seeding
variant arms; by the time S5 arrives to add sampling logic, ~48-72h
of variant reward data will be available.

Without this wire, S5 would need to wait an ADDITIONAL 48-72h after
shipping before it could produce meaningful posteriors.

## Coverage

- variant_type set → variant arm appended
- variant_type=single_clip → arm STILL appended (baseline arm, not skipped)
- variant_type missing/empty → no variant arm appended
- variant_type unknown (typo, stale data) → no variant arm appended (guard)
- Coexists with style + hour arms (all three appear together)
"""

from __future__ import annotations

from unittest.mock import patch

from genlab_core.publishing.feedback_registration import _build_bandit_context


class TestVariantArmAttribution:
    def _call(self, fields, niche_id="gaming", **kw):
        """Common invocation with a stubbed linucb context for isolation."""
        with patch("genlab_core.learning.linucb.build_content_context") as mock_ctx:
            mock_ctx.return_value.tolist.return_value = [0.0] * 6
            return _build_bandit_context(fields, niche_id, **kw)

    def test_series_part_variant_appended(self) -> None:
        ctx = self._call({"variant_type": "series_part"})
        assert ctx is not None
        assert "variant:gaming:series_part" in ctx.get("extra_arms", [])

    def test_question_reveal_variant_appended(self) -> None:
        ctx = self._call({"variant_type": "question_reveal"}, niche_id="ai_creators")
        assert "variant:ai_creators:question_reveal" in ctx.get("extra_arms", [])

    def test_watch_till_end_variant_appended(self) -> None:
        ctx = self._call({"variant_type": "watch_till_end"}, niche_id="sports")
        assert "variant:sports:watch_till_end" in ctx.get("extra_arms", [])

    def test_single_clip_variant_included_as_baseline(self) -> None:
        """single_clip is the default but STILL gets an arm — baseline
        for the bandit to compare against non-default variants. Without
        this, S5 sampling can't compute lift vs the default."""
        ctx = self._call({"variant_type": "single_clip"})
        assert "variant:gaming:single_clip" in ctx.get("extra_arms", [])

    def test_missing_variant_type_no_variant_arm(self) -> None:
        """Legacy blueprints created before S1 have no variant_type field.
        No arm should be appended in that case."""
        ctx = self._call({})
        variant_arms = [a for a in ctx.get("extra_arms", []) if a.startswith("variant:")]
        assert variant_arms == []

    def test_empty_variant_type_no_variant_arm(self) -> None:
        ctx = self._call({"variant_type": ""})
        variant_arms = [a for a in ctx.get("extra_arms", []) if a.startswith("variant:")]
        assert variant_arms == []

    def test_unknown_variant_type_no_arm(self) -> None:
        """Guard against typos / stale data / future variants not in the
        canonical VARIANT_TYPES enum. Prevents polluting bandit_arms
        with garbage arm_ids that consumers would have to filter."""
        ctx = self._call({"variant_type": "not_a_real_variant_typo"})
        variant_arms = [a for a in ctx.get("extra_arms", []) if a.startswith("variant:")]
        assert variant_arms == []

    def test_coexists_with_style_and_hour_arms(self) -> None:
        """Existing style + hour arms MUST continue to fire alongside
        the new variant arm — no regression on their attribution."""
        ctx = self._call(
            {"variant_type": "series_part", "hook_style": "bold_claim"},
            publish_hour=14,
            platform="youtube",
        )
        extra = ctx.get("extra_arms", [])
        assert "style:gaming:bold_claim" in extra
        assert "hour:14:youtube:gaming" in extra
        assert "variant:gaming:series_part" in extra
        assert len(extra) == 3
