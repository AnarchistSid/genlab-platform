"""Pin narration_validator: 5-rule validator + fitting rule.

NARR-01 (2026-08-18). Enforcement in code, not prompt. Every rule
maps to a named degraded_reason slug.
"""
from __future__ import annotations

import pytest

from genlab_core.writing.narration_validator import (
    project_tts_duration_seconds,
    validate_narration_script,
)


class TestFittingRule:
    """Rule 2 (duration_fits). Predictive gate using wpm=150 baseline."""

    def test_borderline_under_budget_passes(self):
        # 30s clip, 2s tail buffer → 28s budget → 70 words at 150 wpm
        text = " ".join(["word"] * 70)
        ok, reason = validate_narration_script(
            text, clip_duration_seconds=30.0,
        )
        assert ok is True, f"unexpected reject: {reason}"

    def test_borderline_over_budget_rejected(self):
        # 30s clip, 2s tail buffer → 28s budget → 70-word cap.
        # 71 words at 150wpm = 28.4s > 28s budget.
        text = " ".join(["word"] * 71)
        ok, reason = validate_narration_script(
            text, clip_duration_seconds=30.0,
        )
        assert ok is False
        assert reason == "script_too_long"

    def test_short_script_short_clip_passes(self):
        # 8s clip, 6s budget, 15 words = 6s at 150wpm
        text = " ".join(["word"] * 15)
        ok, reason = validate_narration_script(
            text, clip_duration_seconds=8.0,
        )
        assert ok is True

    def test_custom_wpm_expands_budget(self):
        # At wpm=180, 84 words = 28s = fits into 28s budget
        text = " ".join(["word"] * 84)
        ok, reason = validate_narration_script(
            text, clip_duration_seconds=30.0, wpm=180,
        )
        assert ok is True

    def test_custom_tail_buffer_shrinks_budget(self):
        # 30s clip, 5s tail buffer → 25s budget → 62 words fits
        text = " ".join(["word"] * 62)
        ok, _ = validate_narration_script(
            text, clip_duration_seconds=30.0, tail_buffer_seconds=5.0,
        )
        assert ok is True
        # 63 words fails
        text_over = " ".join(["word"] * 64)
        ok, reason = validate_narration_script(
            text_over, clip_duration_seconds=30.0, tail_buffer_seconds=5.0,
        )
        assert ok is False
        assert reason == "script_too_long"


class TestEmptyReject:
    """Rule 1 (not_empty). < 20 chars → script_generation_failed."""

    def test_empty_string_rejected(self):
        ok, reason = validate_narration_script("", 30.0)
        assert ok is False
        assert reason == "script_generation_failed"

    def test_whitespace_only_rejected(self):
        ok, reason = validate_narration_script("     \n   \t  ", 30.0)
        assert ok is False
        assert reason == "script_generation_failed"

    def test_too_short_rejected(self):
        ok, reason = validate_narration_script("Hi.", 30.0)
        assert ok is False
        assert reason == "script_generation_failed"

    def test_at_min_chars_passes(self):
        # Exactly 20 chars, fits duration budget
        text = "a" * 20
        ok, _ = validate_narration_script(text, 30.0)
        assert ok is True


class TestUrlReject:
    """Rule 3 (no_urls)."""

    def test_http_url_rejected(self):
        text = "Check this out at http://example.com for more info."
        ok, reason = validate_narration_script(text, 30.0)
        assert ok is False
        assert reason == "script_contained_urls"

    def test_https_url_rejected(self):
        text = "Full story at https://blackboxbrief.com/story details."
        ok, reason = validate_narration_script(text, 30.0)
        assert ok is False
        assert reason == "script_contained_urls"


class TestAffiliateCtaReject:
    """Rule 4 (no_affiliate_ctas). Substring match, case-insensitive."""

    @pytest.mark.parametrize("marker", [
        "shop now for the latest gear",
        "Grab yours today with our discount",
        "Link in bio for the full deal",
        "Use code SAVE20 at checkout",
        "SHOP NOW — limited time",
        "Sponsored by NordVPN today",
        "Check out our store for more",
    ])
    def test_affiliate_marker_rejected(self, marker):
        # Pad to > 20 chars if needed
        text = marker + " and here's why it matters to you today."
        ok, reason = validate_narration_script(text, 30.0)
        assert ok is False, f"expected reject for: {marker!r}"
        assert reason == "script_contained_affiliate_cta"

    def test_no_marker_passes(self):
        text = "OpenAI released a new model that reasons better than the last."
        ok, _ = validate_narration_script(text, 30.0)
        assert ok is True


class TestFirstPersonReject:
    """Rule 5 (no_first_person_experience_claims). Word-boundary match."""

    @pytest.mark.parametrize("verb", [
        "played", "watched", "tried", "tested", "built",
        "created", "made", "designed", "coded", "wrote", "produced",
    ])
    def test_first_person_verbs_rejected(self, verb):
        text = f"I {verb} it last week and here is what happened next."
        ok, reason = validate_narration_script(text, 30.0)
        assert ok is False, f"expected reject for: I {verb}"
        assert reason == "script_first_person_claim"

    def test_third_person_passes(self):
        text = "The creator built this app in a weekend using Claude only."
        ok, _ = validate_narration_script(text, 30.0)
        assert ok is True

    def test_i_without_experience_verb_passes(self):
        # "I" alone isn't banned — only paired with experience verbs
        text = "I think this is the biggest AI shift since GPT-4 landed."
        ok, _ = validate_narration_script(text, 30.0)
        assert ok is True


class TestPassPath:
    """Golden: a well-formed script passes all 5 rules."""

    def test_realistic_ai_creators_script(self):
        text = (
            "OpenAI's new agent can now run entire coding workflows without "
            "human input. What used to take a full sprint takes minutes."
        )
        ok, reason = validate_narration_script(text, 30.0)
        assert ok is True
        assert reason == ""


class TestProjectDuration:
    """The fitting projection is a public helper — used by both the
    validator and (in Phase 3) the post-synth ffprobe comparison."""

    def test_150_wpm_baseline(self):
        text = " ".join(["word"] * 150)
        assert project_tts_duration_seconds(text) == pytest.approx(60.0)

    def test_custom_wpm(self):
        text = " ".join(["word"] * 90)
        # 90 words at 180 wpm = 30s
        assert project_tts_duration_seconds(text, wpm=180) == pytest.approx(30.0)

    def test_zero_wpm_falls_back_to_150(self):
        text = " ".join(["word"] * 150)
        # Defensive: bad config shouldn't div-zero
        assert project_tts_duration_seconds(text, wpm=0) == pytest.approx(60.0)

    def test_empty_text_zero_seconds(self):
        assert project_tts_duration_seconds("") == 0.0
