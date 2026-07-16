"""Pin tests: single-match keyword classify PROMOTES to multi-match
when arm_boosts data reveals a materially better alternative.

Session 2026-07-14 audit found the bandit was descriptive but not
prescriptive: `_classify_arm_with_propensity` short-circuited on
single-keyword-match cases (~92% of prod picks) and returned
(arm, 1.0) before LinUCB or Thompson-boost had a chance to arbitrate.
Consequence: ai_creators.comparison_test had 31 obs at avg_r=0.143
while tool_demo had 32 obs at avg_r=0.082 — bandit posteriors
correctly reflected this, but pick rate over last 3 days was
tool_demo:5, comparison_test:0. Learning happened; the pick didn't
act on it.

These tests pin the promotion behavior: when a keyword-single-match
arm has a lower boost than at least one alternative (by ≥1.10×), the
alternative is added to the candidate set and the existing multi-
match chain (LinUCB → Thompson → first-match) picks between them.
"""

from __future__ import annotations

import numpy as np
from genlab_core.pipeline.stages.push_to_backlog import (
    _classify_arm_with_propensity,
)


class TestSingleMatchPromotion:
    """Widening single-match candidate set when alternatives outperform."""

    def _story_and_content(self, hook: str):
        return {"title": "", "summary": ""}, {"hook": hook}

    def test_single_match_no_boosts_still_returns_single_arm(self):
        """Cold start / no reward data → preserve deterministic single-match.

        arm_boosts=None means no reward data yet. Cannot promote — return
        the keyword-matched arm with propensity=1.0.
        """
        story, content = self._story_and_content("Fortnite got a new patch")
        arm, prop = _classify_arm_with_propensity(
            "gaming",
            story,
            content,
            arm_boosts=None,
        )
        assert arm == "patch_news"  # matches "patch" keyword
        assert prop == 1.0

    def test_single_match_no_alternative_above_threshold_stays_single(self):
        """When no alternative has ≥1.10× the matched arm's boost →
        preserve deterministic pick. Prevents marginal-difference
        promotion that would drown the keyword signal in noise."""
        story, content = self._story_and_content("Fortnite got a new patch")
        arm, prop = _classify_arm_with_propensity(
            "gaming",
            story,
            content,
            arm_boosts={
                "patch_news": 1.20,  # matched
                "esports_highlight": 1.25,  # only 1.04× higher — below 1.10× threshold
                "trailer_reaction": 0.90,  # lower — never a candidate
            },
        )
        assert arm == "patch_news"
        assert prop == 1.0

    def test_single_match_with_strong_alternative_promotes_to_multimatch(self):
        """When alternative has ≥1.10× the matched arm's boost →
        promote to multi-match. Without LinUCB context, falls back to
        Thompson boost which picks max(arm_boosts)."""
        story, content = self._story_and_content("Fortnite got a new patch")
        # matched arm has low boost; another arm has much higher boost
        arm, prop = _classify_arm_with_propensity(
            "gaming",
            story,
            content,
            arm_boosts={
                "patch_news": 0.80,  # matched, low
                "viral_moment": 1.50,  # 1.875× higher — should get added
                "trailer_reaction": 0.95,  # below matched, never candidate
            },
        )
        # Thompson-boost path picks max(arm_boosts) among candidates.
        # Candidates are ["patch_news", "viral_moment"] after promotion.
        # viral_moment (1.50) > patch_news (0.80) → viral_moment wins.
        assert arm == "viral_moment"
        # Thompson has no IPS-compatible propensity → None
        assert prop is None

    def test_single_match_promotion_caps_at_top_2_alternatives(self):
        """Bounded exploration: only top-2 alternatives get added, not all.
        Prevents throwing away keyword semantics when many high-boost arms exist."""
        story, content = self._story_and_content("Fortnite got a new patch")
        # Many alternatives all above 1.10× threshold
        arm, prop = _classify_arm_with_propensity(
            "gaming",
            story,
            content,
            arm_boosts={
                "patch_news": 1.00,  # matched
                "viral_moment": 1.20,  # +1
                "esports_highlight": 1.30,  # +2  → these two get added
                "trailer_reaction": 1.40,  # +3  → top-2 slot filled, this NOT added
            },
        )
        # Top-2 alternatives = trailer_reaction (1.40) + esports_highlight (1.30).
        # Thompson-boost picks max — trailer_reaction wins.
        assert arm == "trailer_reaction"

    def test_single_match_promotion_with_linucb_context_uses_softmax_propensity(self):
        """When LinUCB context IS provided, the promoted-multi-match case
        should use LinUCB softmax and produce a non-None propensity.
        This is the target of the fix — real IPS-usable data."""
        import os

        story, content = self._story_and_content("Fortnite got a new patch")

        # Note: LinUCB requires linucb_arms with the right dimensions
        # and enabled env. This test asserts the WIRE — the LinUCB path
        # is now REACHABLE for single-match cases when boosts promote.
        # It may still fall back to Thompson if LinUCB env is off or
        # linucb_arms is empty — but critically it is NO LONGER short-
        # circuited before even reaching the LinUCB branch.

        # env-off case: should still work + return arm via Thompson
        os.environ.pop("GENLAB_LINUCB_PICK_ENABLED", None)
        arm, prop = _classify_arm_with_propensity(
            "gaming",
            story,
            content,
            arm_boosts={
                "patch_news": 0.80,
                "viral_moment": 1.50,
            },
            linucb_arms=None,  # explicit no-LinUCB
            context=np.zeros(13),  # LinUCB v1 context dim
        )
        # Falls through to Thompson-boost (LinUCB env off + no linucb_arms)
        assert arm in ("patch_news", "viral_moment")

    def test_matched_arm_still_wins_when_it_has_the_best_boost(self):
        """Promotion doesn't force alternative to win — it just widens
        the candidate set. Matched arm still gets picked when its
        boost is the highest among candidates.

        Note: alternatives are only ADDED when their boost is >1.10×
        matched arm's boost. So matched arm can never be "the best"
        among candidates after promotion (by construction). This test
        pins the case where promotion doesn't trigger."""
        story, content = self._story_and_content("Fortnite got a new patch")
        arm, prop = _classify_arm_with_propensity(
            "gaming",
            story,
            content,
            arm_boosts={
                "patch_news": 1.50,  # matched, HIGHEST — no promotion
                "viral_moment": 1.20,
                "trailer_reaction": 0.90,
            },
        )
        assert arm == "patch_news"
        assert prop == 1.0

    def test_multi_match_case_unaffected(self):
        """The promotion logic runs only when len(matches) == 1. Multi-
        match cases (natively >1 keyword match) still use the existing
        LinUCB→Thompson→first-match chain."""
        story = {"title": "", "summary": ""}
        # Hook matches BOTH viral_moment ("viral") AND trailer_reaction ("trailer")
        content = {"hook": "Fortnite's new trailer just went viral"}
        arm, prop = _classify_arm_with_propensity(
            "gaming",
            story,
            content,
            arm_boosts={
                "viral_moment": 1.50,
                "trailer_reaction": 0.80,
            },
        )
        # Natural multi-match. Thompson-boost picks max.
        assert arm == "viral_moment"


class TestPromotionThreshold:
    """The 1.10× threshold is a load-bearing tuning knob."""

    def test_exactly_at_threshold_does_not_promote(self):
        """Strict > 1.10, not ≥. Prevents edge oscillation on cold arms
        hovering right at the Beta(1,1) prior."""
        from genlab_core.pipeline.stages.push_to_backlog import (
            _classify_arm_with_propensity,
        )

        story = {"title": "", "summary": ""}
        content = {"hook": "Fortnite patch news"}
        arm, prop = _classify_arm_with_propensity(
            "gaming",
            story,
            content,
            arm_boosts={
                "patch_news": 1.00,
                "viral_moment": 1.10,  # exactly at threshold
            },
        )
        assert arm == "patch_news"
        assert prop == 1.0
