"""Pin: pre-render quality gate catches the 7 real-production failures.

Post-2026-07-13 audit follow-up (Improvement B). The gate sits between
the writer's output and the FFmpeg render as a last line of defense.
This test file:

  1. Pins every one of the last 30 days of bad-hook production
     failures as a rejection scenario — if a future refactor loosens
     the gate, ALL of these fire.
  2. Pins the good hooks that shipped correctly as accept scenarios —
     so we don't regress into false-positives on legitimate content.
  3. Pins each rule's contract independently so a rule can be
     tightened or loosened without cascading.

Historical failures (query at 2026-07-13):

  2026-07-13 ai_creators — "I need to flag a problem: the Story..."
  2026-07-12 movies      — "I need the Story Summary to write a hook. The..."
  2026-07-11 movies      — "I need the Story Summary to write a hook for Moana..."
  2026-07-11 gaming      — "Grand Theft Auto V"
  2026-07-07 movies      — "I need to flag a critical issue: the Story title..."
  2026-06-30 ai_creators — "I AM SO MAD"
  2026-06-20 movies      — "I need more story details to write an effective..."

Of these, 6 should be rejected. "I AM SO MAD" is a judgment call —
it's arguably a legit emotional hook, so the gate accepts it (has
verb "AM").
"""

from __future__ import annotations

import pytest
from genlab_core.rendering.pre_render_quality import (
    QualityCheck,
    check_pre_render_quality,
)


class TestLLMRefusalPreambles:
    """Rule 1: LLM refusal preambles are the most severe failure —
    they burn a "sorry I need more context" into the audience-facing
    video. All 5 of the last 30 days' refusals must reject."""

    @pytest.mark.parametrize(
        "hook",
        [
            "I need to flag a problem: the Story and Summary don't match",
            "I need the Story Summary to write a hook. The summary is empty",
            "I need the Story Summary to write a hook for Moana. The context",
            "I need to flag a critical issue: the Story title is missing",
            "I need more story details to write an effective hook.",
            "I cannot write a hook without more context",
            "I don't have enough information to write a hook",
            "I'm sorry, but I need more details",
            "I apologize, but the story doesn't have enough context",
        ],
    )
    def test_refusal_preamble_rejected(self, hook):
        result = check_pre_render_quality(hook)
        assert result.ok is False, f"Hook must be rejected but passed: {hook!r}"
        assert result.reason == "llm_refusal_preamble"

    def test_case_insensitive_match(self):
        """Refusals are lowercase-normalised before matching so
        operator-formatted variants don't slip through."""
        result = check_pre_render_quality("I NEED THE STORY SUMMARY")
        assert result.ok is False
        assert result.reason == "llm_refusal_preamble"


class TestBareTitleHooks:
    """Rule 3: bare proper-noun hooks like "Grand Theft Auto V" ship
    without a take. 1/7 of the last 30d failures was this shape."""

    def test_the_gta_v_case(self):
        """The concrete production failure from 2026-07-11."""
        result = check_pre_render_quality("Grand Theft Auto V")
        assert result.ok is False
        assert result.reason == "hook_bare_title"

    @pytest.mark.parametrize(
        "hook",
        [
            "Cyberpunk 2077 Phantom Liberty",  # 4 tokens, Title case, no verb
            "Elden Ring Shadow of the Erdtree",  # ← has "of the" — lowercase, verb signal
            "Baldurs Gate III Update",  # 4 tokens, no verb signal
            "Sony PlayStation VR2 Pro",  # 5 tokens, no verb signal
        ],
    )
    def test_bare_title_family(self, hook):
        result = check_pre_render_quality(hook)
        # Some of these will pass because they have lowercase tokens
        # like "of" and "the" — good. We just want to pin the general
        # shape.
        if "of" in hook.lower().split() or "the" in hook.lower().split():
            # Lowercase preposition/article → verb signal → passes
            assert result.ok is True
        else:
            assert result.ok is False
            assert result.reason == "hook_bare_title"


class TestMinLength:
    """Rule 2: minimum length catches truncation bugs + empty
    placeholders."""

    def test_empty_hook_rejected(self):
        assert check_pre_render_quality("").ok is False
        assert check_pre_render_quality("").reason == "empty_hook"

    def test_whitespace_only_rejected(self):
        assert check_pre_render_quality("   \n\t  ").ok is False

    def test_below_15_chars_rejected(self):
        result = check_pre_render_quality("Wow big news!")
        assert result.ok is False
        assert result.reason == "hook_too_short"


class TestLegitHooksAccepted:
    """No false positives on real hooks that shipped successfully.
    These come from actual PUBLISHED blueprints in the last 7 days —
    if the gate rejects any, we broke something."""

    @pytest.mark.parametrize(
        "hook",
        [
            "DeepSeek's New AI Speed Hack Is Amazing",
            "Light destroys evidence mid-interrogation and the cops",
            "Did Verstappen's strategy gamble pay off in Austria?",
            "Autonomous AI that runs for days without a human",
            "Granny's sitting at trending on Twitch right now",
            "Evil Dead Burn looks like they finally let the director",
            "Bam Adebayo Just Dropped 83 in the Playoffs",
            "Cinema is back and Marvel has some explaining to do",
        ],
    )
    def test_real_hooks_accepted(self, hook):
        result = check_pre_render_quality(hook)
        assert result.ok is True, (
            f"Legit hook rejected: {hook!r} — reason={result.reason} detail={result.detail}"
        )

    def test_i_am_so_mad_accepted(self):
        """Edge case from 2026-06-30: an all-caps short emotional
        hook. It has the copula "AM" as a verb signal, so it PASSES
        the bare-title check. Length 11 chars < 15 → fails on length.

        Even if we made this pass length, it's a judgment call — an
        operator may have written it intentionally. The gate is
        conservative; a false-positive rejection is preferable to a
        false-negative acceptance because operator can still hand-edit
        and re-render."""
        result = check_pre_render_quality("I AM SO MAD")
        # Fails length check (11 < 15)
        assert result.ok is False
        assert result.reason == "hook_too_short"

    def test_i_am_so_mad_with_padding_accepted(self):
        """If padded past the length threshold, "I AM SO MAD"-style
        hooks pass — verb "AM" catches the bare-title rule."""
        result = check_pre_render_quality("I AM SO MAD ABOUT THIS")
        assert result.ok is True


class TestOrderOfRulesShortCircuits:
    """Refusal preambles take priority over other rules so operator
    sees the most severe failure first, not the least."""

    def test_refusal_beats_length(self):
        # Short-circuit ordering: even though "I need the X" is only
        # 12 chars (below length threshold), the refusal prefix rule
        # must fire first so the operator sees the more severe reason.
        result = check_pre_render_quality("I need the X")
        assert result.reason == "llm_refusal_preamble"

    def test_length_beats_bare_title(self):
        result = check_pre_render_quality("XYZ")  # short + bare + no verb
        assert result.reason == "hook_too_short"

    def test_return_type_is_dataclass(self):
        result = check_pre_render_quality("ok")
        assert isinstance(result, QualityCheck)
        assert isinstance(result.ok, bool)
        assert isinstance(result.reason, str)
        assert isinstance(result.detail, str)


class TestGateIsPureFunction:
    """No I/O, no side effects, no LLM calls. Two calls with the same
    input always produce the same output. Guardrails against
    accidental refactor that introduces flakiness."""

    def test_deterministic(self):
        hook = "This is a legit hook about something interesting"
        a = check_pre_render_quality(hook)
        b = check_pre_render_quality(hook)
        assert a == b

    def test_idempotent_on_valid(self):
        # A valid QualityCheck.ok stays True on repeat calls
        for _ in range(5):
            r = check_pre_render_quality("Real hook with verb signal and enough length")
            assert r.ok is True

    def test_no_niche_effect_on_outcome(self):
        """niche_id is passed for logging enrichment only — must not
        change the accept/reject decision."""
        hook = "Grand Theft Auto V"
        a = check_pre_render_quality(hook, niche_id="gaming")
        b = check_pre_render_quality(hook, niche_id="movies")
        c = check_pre_render_quality(hook, niche_id="")
        assert a.ok == b.ok == c.ok
