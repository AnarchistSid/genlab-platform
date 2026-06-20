"""Tests for genlab_core.intelligence.hook_validator."""

import pytest
from genlab_core.intelligence.hook_validator import (
    HookFailure,
    HookValidationResult,
    HookValidator,
)


@pytest.fixture
def validator():
    return HookValidator()


# ---------------------------------------------------------------------------
# Rule 1: Too short
# ---------------------------------------------------------------------------


class TestTooShort:
    def test_two_words_fails(self, validator):
        result = validator.validate("Hello world", "instagram")
        assert HookFailure.TOO_SHORT in result.failures

    def test_three_words_passes(self, validator):
        result = validator.validate("This is fine", "instagram")
        assert HookFailure.TOO_SHORT not in result.failures

    def test_empty_hook_fails(self, validator):
        result = validator.validate("", "instagram")
        assert HookFailure.TOO_SHORT in result.failures


# ---------------------------------------------------------------------------
# Rule 2: Too long (platform limit)
# ---------------------------------------------------------------------------


class TestTooLong:
    def test_exceeds_twitter_limit(self, validator):
        hook = "x" * 281
        result = validator.validate(hook, "twitter")
        assert HookFailure.TOO_LONG in result.failures

    def test_within_twitter_limit(self, validator):
        hook = "This is a reasonably short hook for Twitter"
        result = validator.validate(hook, "twitter")
        assert HookFailure.TOO_LONG not in result.failures

    def test_youtube_title_limit(self, validator):
        hook = "x" * 101
        result = validator.validate(hook, "youtube")
        assert HookFailure.TOO_LONG in result.failures

    def test_unknown_platform_no_limit(self, validator):
        hook = "x" * 5000
        result = validator.validate(hook, "custom_platform")
        assert HookFailure.TOO_LONG not in result.failures


# ---------------------------------------------------------------------------
# Rule 3: Markdown artifacts
# ---------------------------------------------------------------------------


class TestMarkdownArtifacts:
    def test_bold_markdown(self, validator):
        result = validator.validate("This is **bold text** in a hook", "instagram")
        assert HookFailure.MARKDOWN_ARTIFACTS in result.failures

    def test_italic_markdown(self, validator):
        result = validator.validate("This is *italic text* in hooks", "instagram")
        assert HookFailure.MARKDOWN_ARTIFACTS in result.failures

    def test_no_markdown_passes(self, validator):
        result = validator.validate("This is a clean hook text", "instagram")
        assert HookFailure.MARKDOWN_ARTIFACTS not in result.failures


# ---------------------------------------------------------------------------
# Rule 4: Reddit formatting
# ---------------------------------------------------------------------------


class TestRedditFormatting:
    def test_subreddit_reference(self, validator):
        result = validator.validate("Insane play in ranked /r/leagueoflegends today", "instagram")
        assert HookFailure.REDDIT_FORMATTING in result.failures

    def test_user_reference(self, validator):
        result = validator.validate("Check out what /u/coolplayer did today", "instagram")
        assert HookFailure.REDDIT_FORMATTING in result.failures

    def test_no_reddit_formatting(self, validator):
        result = validator.validate("Insane play in League of Legends ranked", "instagram")
        assert HookFailure.REDDIT_FORMATTING not in result.failures


# ---------------------------------------------------------------------------
# Rule 5: Excessive punctuation
# ---------------------------------------------------------------------------


class TestExcessivePunctuation:
    def test_triple_exclamation(self, validator):
        result = validator.validate("This is insane!!! Watch now", "instagram")
        assert HookFailure.EXCESSIVE_PUNCT in result.failures

    def test_triple_question(self, validator):
        result = validator.validate("Why is nobody talking about this???", "instagram")
        assert HookFailure.EXCESSIVE_PUNCT in result.failures

    def test_double_exclamation_ok(self, validator):
        result = validator.validate("This is amazing!! So good", "instagram")
        assert HookFailure.EXCESSIVE_PUNCT not in result.failures


# ---------------------------------------------------------------------------
# Rule 6: Incomplete sentence
# ---------------------------------------------------------------------------


class TestIncompleteSentence:
    def test_trailing_ellipsis_allowed(self, validator):
        """Deliberate '...' is used by content writers for hook truncation — allowed."""
        result = validator.validate("When you see what happens next...", "instagram")
        assert result.passed

    def test_trailing_many_dots_rejected(self, validator):
        """4+ dots is a genuine truncation artifact — rejected."""
        result = validator.validate("When you see what happens next....", "instagram")
        assert HookFailure.INCOMPLETE_SENTENCE in result.failures

    def test_trailing_colon(self, validator):
        result = validator.validate("The best gaming moments of 2025:", "instagram")
        assert HookFailure.INCOMPLETE_SENTENCE in result.failures

    def test_dangling_dash(self, validator):
        result = validator.validate("This game just changed everything —", "instagram")
        assert HookFailure.INCOMPLETE_SENTENCE in result.failures

    def test_ends_with_stopword(self, validator):
        result = validator.validate("The best thing you can do for", "instagram")
        assert HookFailure.INCOMPLETE_SENTENCE in result.failures

    def test_complete_sentence_passes(self, validator):
        result = validator.validate("This game just changed everything", "instagram")
        assert HookFailure.INCOMPLETE_SENTENCE not in result.failures


# ---------------------------------------------------------------------------
# Rule 7: Contains URL
# ---------------------------------------------------------------------------


class TestContainsUrl:
    def test_http_url(self, validator):
        result = validator.validate("Check this out https://example.com for more", "instagram")
        assert HookFailure.CONTAINS_URL in result.failures

    def test_www_url(self, validator):
        result = validator.validate("Visit www.example.com for details today", "instagram")
        assert HookFailure.CONTAINS_URL in result.failures

    def test_no_url_passes(self, validator):
        result = validator.validate("This game just hit different after the update", "instagram")
        assert HookFailure.CONTAINS_URL not in result.failures


# ---------------------------------------------------------------------------
# Rule 8: Emoji spam
# ---------------------------------------------------------------------------


class TestEmojiSpam:
    def test_excessive_emojis(self, validator):
        # 8+ emoji sequences
        hook = "This " + " ".join(["🔥"] * 8) + " is insane"
        result = validator.validate(hook, "instagram")
        assert HookFailure.EMOJI_SPAM in result.failures

    def test_moderate_emojis_ok(self, validator):
        result = validator.validate("This is fire 🔥🔥🔥 no cap", "instagram")
        assert HookFailure.EMOJI_SPAM not in result.failures


# ---------------------------------------------------------------------------
# Rule 9: Mixed CAPS/lowercase
# ---------------------------------------------------------------------------


class TestMixedCaps:
    def test_broken_template_pattern(self, validator):
        result = validator.validate("WOULD YOU USE salesforce slackbot thing now", "instagram")
        assert HookFailure.MIXED_CAPS in result.failures

    def test_intentional_caps_ok(self, validator):
        # Known acronyms like AI, GPT shouldn't trigger
        result = validator.validate(
            "This AI tool just changed everything for developers", "instagram"
        )
        assert HookFailure.MIXED_CAPS not in result.failures


# ---------------------------------------------------------------------------
# Batch and filter methods
# ---------------------------------------------------------------------------


class TestBatchAndFilter:
    def test_validate_batch(self, validator):
        hooks = ["Good hook text here", "**bad** hook text", "Also fine and good"]
        results = validator.validate_batch(hooks, "instagram")
        assert len(results) == 3
        assert results[0].passed
        assert not results[1].passed
        assert results[2].passed

    def test_filter_valid(self, validator):
        hooks = [
            "This is a valid hook",
            "**markdown** breaks this hook",
            "Another valid hook text",
            "Too long " + "x" * 300,
        ]
        valid = validator.filter_valid(hooks, "twitter")
        assert "This is a valid hook" in valid
        assert "Another valid hook text" in valid
        assert len(valid) == 2


# ---------------------------------------------------------------------------
# Integration: multiple failures
# ---------------------------------------------------------------------------


class TestMultipleFailures:
    def test_hook_with_multiple_issues(self, validator):
        hook = "**bold** /r/gaming!!!"
        result = validator.validate(hook, "instagram")
        assert not result.passed
        assert HookFailure.MARKDOWN_ARTIFACTS in result.failures
        assert HookFailure.REDDIT_FORMATTING in result.failures
        assert HookFailure.EXCESSIVE_PUNCT in result.failures


# ---------------------------------------------------------------------------
# HookValidationResult dataclass
# ---------------------------------------------------------------------------


class TestHookValidationResult:
    def test_default_is_passed(self):
        r = HookValidationResult(hook="test", platform="instagram")
        assert r.passed is True
        assert r.failures == []
        assert r.warnings == []


class TestLLMErrorResponseDetection:
    """Pin the 2026-06-21 rule-10 fix — LLM error/refusal response detection.

    Prod symptom (R7 browser verification, 2026-06-20): SpliceReel YouTube
    short ``QrNDe-egDrg`` shipped with title
    ``"I need more story details to write an effective hook...."`` — a
    literal LLM meta-response (asking for clarification instead of
    producing the requested hook). The 9 form-based rules (TOO_SHORT,
    MARKDOWN, REDDIT, PUNCT, INCOMPLETE_SENTENCE, URL, EMOJI_SPAM,
    MIXED_CAPS) all PASS this string because it's syntactically valid:
    long enough, no markdown, complete sentence, no URL, etc.

    Rule 10 anchors to the START of the string (``re.match``) — minimizes
    false positives on legitimate content that happens to contain LLM-
    error keywords mid-sentence (e.g., ``"Why I cannot stop watching this"``
    starts with "Why", NOT with "I cannot ").
    """

    def test_exact_prod_symptom_rejected(self, validator):
        """The headline pin: the exact string published as YouTube title
        in prod (SpliceReel ``QrNDe-egDrg``, 2026-06-20). Must be flagged
        as LLM_ERROR_RESPONSE so the YT title path can fall back."""
        hook = "I need more story details to write an effective hook...."
        result = validator.validate(hook, "youtube")
        assert HookFailure.LLM_ERROR_RESPONSE in result.failures, (
            "The exact 2026-06-20 prod-leak string MUST trip rule 10. "
            "Pre-fix, this string passed all 9 form rules and shipped as "
            "the YouTube video title."
        )
        assert not result.passed

    def test_cannot_refusal_detected(self, validator):
        hook = "I cannot write a hook without more context about the story"
        result = validator.validate(hook, "youtube")
        assert HookFailure.LLM_ERROR_RESPONSE in result.failures

    def test_apologize_response_detected(self, validator):
        hook = "I apologize, but I can't generate a hook for this story"
        result = validator.validate(hook, "youtube")
        assert HookFailure.LLM_ERROR_RESPONSE in result.failures

    def test_as_an_ai_disclaimer_detected(self, validator):
        # Comma anchors to the disclaimer phrasing — distinguishes from
        # legitimate phrases like "As an AI demo lover"
        hook = "As an AI, I cannot generate misleading hooks"
        result = validator.validate(hook, "youtube")
        assert HookFailure.LLM_ERROR_RESPONSE in result.failures

    def test_without_more_context_detected(self, validator):
        hook = "Without more context, I cannot write an effective hook"
        result = validator.validate(hook, "youtube")
        assert HookFailure.LLM_ERROR_RESPONSE in result.failures

    def test_unable_to_generate_detected(self, validator):
        hook = "Unable to generate a hook from the provided text"
        result = validator.validate(hook, "youtube")
        assert HookFailure.LLM_ERROR_RESPONSE in result.failures

    def test_insufficient_information_detected(self, validator):
        hook = "Insufficient story details to write a compelling hook"
        result = validator.validate(hook, "youtube")
        assert HookFailure.LLM_ERROR_RESPONSE in result.failures

    def test_im_sorry_detected(self, validator):
        hook = "I'm sorry, but I need more information to proceed"
        result = validator.validate(hook, "youtube")
        assert HookFailure.LLM_ERROR_RESPONSE in result.failures

    def test_legitimate_hook_starting_with_why_i_cannot_passes(self, validator):
        """**CRITICAL** false-positive guard: legitimate hooks that happen
        to contain LLM-error keywords MID-sentence must NOT be rejected.
        Rule 10 is anchored to the START — content like
        ``"Why I cannot stop watching this trailer"`` (starts with "Why",
        legitimate hook) MUST pass.
        """
        hook = "Why I cannot stop watching this trailer"
        result = validator.validate(hook, "youtube")
        assert HookFailure.LLM_ERROR_RESPONSE not in result.failures, (
            "Legitimate hooks containing LLM-keywords MID-sentence must "
            "NOT trip rule 10. Anchoring at ^ minimizes false positives."
        )

    def test_legitimate_hook_as_an_ai_without_comma_passes(self, validator):
        """Pin the AI-disclaimer-vs-legitimate distinction: "As an AI demo
        lover" is legitimate; only "As an AI," (with comma) is the
        disclaimer pattern."""
        hook = "As an AI demo lover here's what blew my mind"
        result = validator.validate(hook, "youtube")
        assert HookFailure.LLM_ERROR_RESPONSE not in result.failures, (
            "Legitimate AI-related hooks must pass — only the comma-"
            "anchored disclaimer pattern (As an AI,) trips rule 10."
        )

    def test_legitimate_hook_about_need_passes(self, validator):
        """Hooks that legitimately use 'need' or 'cannot' mid-sentence
        must pass."""
        hook = "Players still need a fix for this annoying bug"
        result = validator.validate(hook, "youtube")
        assert HookFailure.LLM_ERROR_RESPONSE not in result.failures

    def test_case_insensitive_detection(self, validator):
        """LLM error responses can come in any casing (some LLMs return
        title-case)."""
        hook = "I Need More Story Details To Write An Effective Hook"
        result = validator.validate(hook, "youtube")
        assert HookFailure.LLM_ERROR_RESPONSE in result.failures

    def test_leading_whitespace_stripped(self, validator):
        """Pre-fix the LLM occasionally returned the error string with
        leading whitespace; the strip() in _check_llm_error_response
        ensures detection still fires."""
        hook = "   I need more story details to write an effective hook"
        result = validator.validate(hook, "youtube")
        assert HookFailure.LLM_ERROR_RESPONSE in result.failures

    def test_rule_10_independent_of_other_rules(self, validator):
        """When ONLY rule 10 trips, that's the only failure listed.
        Pin against accidental coupling with other rules."""
        # "I cannot generate hooks for this story" — long enough, no
        # markdown, no URL, no emoji spam, complete sentence — only
        # trips rule 10.
        hook = "I cannot generate hooks for this story"
        result = validator.validate(hook, "youtube")
        assert result.failures == [HookFailure.LLM_ERROR_RESPONSE], (
            f"Expected ONLY LLM_ERROR_RESPONSE failure; got {result.failures}. "
            f"If others fire too, the hook string also trips another rule "
            f"and needs adjustment to keep this test focused."
        )
