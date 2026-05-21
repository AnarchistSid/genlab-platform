"""Tests for hybrid auto-reply confidence routing."""

from __future__ import annotations

from genlab_core.engagement.comment_processor import classify_reply_action


class TestConfidenceRouting:
    def test_high_confidence_safe_reply_auto(self):
        """High confidence + low toxicity + safe pattern -> auto-post."""
        action = classify_reply_action(
            reply_text="thanks! glad you liked it",
            confidence=0.9,
            toxicity=0.05,
        )
        assert action == "auto"

    def test_medium_confidence_queued(self):
        """Medium confidence -> review queue."""
        action = classify_reply_action(
            reply_text="That's an interesting take, here's why I think so",
            confidence=0.7,
            toxicity=0.1,
        )
        assert action == "review"

    def test_low_confidence_discarded(self):
        """Low confidence -> discard."""
        action = classify_reply_action(
            reply_text="whatever",
            confidence=0.3,
            toxicity=0.1,
        )
        assert action == "discard"

    def test_high_toxicity_discarded(self):
        """High toxicity overrides confidence -> discard."""
        action = classify_reply_action(
            reply_text="great point!",
            confidence=0.95,
            toxicity=0.4,
        )
        assert action == "discard"

    def test_long_reply_not_auto(self):
        """Replies > 100 chars are not auto-posted (too risky)."""
        action = classify_reply_action(
            reply_text="A" * 150,
            confidence=0.9,
            toxicity=0.05,
        )
        assert action == "review"

    def test_edge_confidence_exactly_085_auto(self):
        """Confidence == 0.85 boundary is auto when safe."""
        action = classify_reply_action(
            reply_text="exactly!",
            confidence=0.85,
            toxicity=0.0,
        )
        assert action == "auto"

    def test_edge_confidence_just_below_085_review(self):
        """Confidence just below 0.85 -> review."""
        action = classify_reply_action(
            reply_text="exactly!",
            confidence=0.84,
            toxicity=0.0,
        )
        assert action == "review"

    def test_edge_toxicity_exactly_030_discard(self):
        """Toxicity >= 0.3 -> discard."""
        action = classify_reply_action(
            reply_text="great point!",
            confidence=0.9,
            toxicity=0.3,
        )
        assert action == "discard"

    def test_edge_toxicity_just_below_030_review(self):
        """Toxicity below 0.3 but confidence < 0.85 -> review."""
        action = classify_reply_action(
            reply_text="That was unexpected",
            confidence=0.6,
            toxicity=0.29,
        )
        assert action == "review"

    def test_edge_confidence_exactly_050_review(self):
        """Confidence == 0.5 -> review (not discard)."""
        action = classify_reply_action(
            reply_text="That was neat",
            confidence=0.5,
            toxicity=0.1,
        )
        assert action == "review"

    def test_edge_confidence_just_below_050_discard(self):
        """Confidence below 0.5 -> discard."""
        action = classify_reply_action(
            reply_text="That was neat",
            confidence=0.49,
            toxicity=0.1,
        )
        assert action == "discard"

    def test_emoji_only_reply_auto(self):
        """Emoji-only replies at high confidence are auto."""
        action = classify_reply_action(
            reply_text="\U0001f525\U0001f525\U0001f525",
            confidence=0.9,
            toxicity=0.01,
        )
        assert action == "auto"

    def test_cta_reply_auto(self):
        """CTA patterns (check out, subscribe) at high confidence are auto."""
        action = classify_reply_action(
            reply_text="check out our latest!",
            confidence=0.9,
            toxicity=0.02,
        )
        assert action == "auto"

    def test_agreement_pattern_auto(self):
        """Agreement patterns (fr, ikr, so true) are safe for auto."""
        action = classify_reply_action(
            reply_text="fr fr",
            confidence=0.9,
            toxicity=0.01,
        )
        assert action == "auto"

    def test_unsafe_pattern_with_high_confidence_review(self):
        """High confidence but not matching safe pattern -> review."""
        action = classify_reply_action(
            reply_text="I think the developer should reconsider their approach",
            confidence=0.9,
            toxicity=0.05,
        )
        assert action == "review"

    def test_toxicity_between_015_and_030_review(self):
        """Toxicity between 0.15 and 0.3 forces review even if confidence high."""
        action = classify_reply_action(
            reply_text="thanks!",
            confidence=0.95,
            toxicity=0.2,
        )
        assert action == "review"
