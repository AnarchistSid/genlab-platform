"""Tests for gaming-specific hook validator."""

import pytest
from niches.gaming.hooks.hook_validator import (
    GamingHookFailure,
    GamingHookValidator,
)


@pytest.fixture
def validator():
    return GamingHookValidator()


class TestMarkdownDetection:
    def test_bold_markdown_fails(self, validator):
        result = validator.validate("**INSANE** play in ranked match")
        assert not result.passed
        assert any("markdown" in f for f in result.all_issues)

    def test_italic_markdown_fails(self, validator):
        result = validator.validate("This *crazy* update changes everything for gamers")
        assert not result.passed

    def test_clean_hook_passes_markdown(self, validator):
        result = validator.validate("This update changes everything for gamers right now")
        markdown_issues = [f for f in result.all_issues if "markdown" in f]
        assert not markdown_issues


class TestRedditPhrases:
    def test_subreddit_fails(self, validator):
        result = validator.validate("r/gaming just found out something huge about the new patch")
        assert not result.passed
        assert GamingHookFailure.REDDIT_PHRASE in result.gaming_failures

    def test_user_reference_fails(self, validator):
        result = validator.validate("/u/player123 discovered a game-breaking exploit today")
        assert not result.passed
        # Caught by universal validator as reddit_formatting
        assert any("reddit" in issue for issue in result.all_issues)

    def test_til_fails(self, validator):
        result = validator.validate("TIL this character has a hidden ability in the latest patch")
        assert not result.passed
        assert GamingHookFailure.REDDIT_PHRASE in result.gaming_failures


class TestFormalOpeners:
    def test_formal_opener_fails(self, validator):
        result = validator.validate("We are pleased to announce the new character roster update")
        assert not result.passed
        assert GamingHookFailure.FORMAL_OPENER in result.gaming_failures

    def test_announcement_opener_fails(self, validator):
        result = validator.validate("It has been announced that the DLC drops next month")
        assert not result.passed
        assert GamingHookFailure.FORMAL_OPENER in result.gaming_failures


class TestWeakOpeners:
    def test_so_opener_fails(self, validator):
        result = validator.validate("So apparently this new gun is completely broken right now")
        assert not result.passed
        assert GamingHookFailure.WEAK_OPENER in result.gaming_failures

    def test_basically_opener_fails(self, validator):
        result = validator.validate("Basically every pro player is switching to this loadout now")
        assert not result.passed
        assert GamingHookFailure.WEAK_OPENER in result.gaming_failures


class TestNewsPrefix:
    def test_breaking_prefix_fails(self, validator):
        result = validator.validate("BREAKING: Riot just dropped the biggest patch of the year")
        assert not result.passed
        assert GamingHookFailure.NEWS_PREFIX in result.gaming_failures

    def test_just_in_prefix_fails(self, validator):
        result = validator.validate("JUST IN: The ranked season has been extended two weeks")
        assert not result.passed
        assert GamingHookFailure.NEWS_PREFIX in result.gaming_failures


class TestLengthLimits:
    def test_too_short_fails(self, validator):
        result = validator.validate("Game update")
        assert not result.passed
        assert GamingHookFailure.TOO_SHORT in result.gaming_failures

    def test_too_long_fails(self, validator):
        long_hook = "A" * 151
        result = validator.validate(long_hook)
        assert not result.passed
        assert GamingHookFailure.TOO_LONG in result.gaming_failures

    def test_valid_length_passes(self, validator):
        result = validator.validate("The new ranked season just dropped today")
        length_issues = [
            f
            for f in result.gaming_failures
            if f in (GamingHookFailure.TOO_SHORT, GamingHookFailure.TOO_LONG)
        ]
        assert not length_issues


class TestClean:
    def test_strips_markdown(self, validator):
        cleaned = validator.clean("**INSANE** play in ranked match")
        assert "**" not in cleaned
        assert "INSANE" in cleaned

    def test_strips_news_prefix(self, validator):
        cleaned = validator.clean("BREAKING: Riot drops biggest patch ever")
        assert not cleaned.startswith("BREAKING")
        assert "Riot" in cleaned

    def test_strips_weak_opener(self, validator):
        cleaned = validator.clean("So apparently this gun is broken")
        assert not cleaned.startswith("So ")

    def test_strips_reddit_phrase(self, validator):
        cleaned = validator.clean("r/gaming found a hidden easter egg today")
        assert "r/gaming" not in cleaned

    def test_collapses_whitespace(self, validator):
        cleaned = validator.clean("BREAKING:  r/gaming  found  it")
        assert "  " not in cleaned


class TestPassingHooks:
    """Hooks that should pass all checks."""

    @pytest.mark.parametrize(
        "hook",
        [
            "This ranked season changes everything now",
            "Gamers are losing it over this mechanic",
            "Devs just shadow-dropped the top feature",
            "Every pro is switching to this loadout",
        ],
    )
    def test_valid_gaming_hooks(self, validator, hook):
        result = validator.validate(hook)
        assert result.passed, f"Hook should pass: {hook!r}, issues: {result.all_issues}"


class TestHookRetry:
    """Test that the retry mechanism in write_gaming_content works conceptually."""

    def test_clean_salvages_bad_hook(self, validator):
        """A bad hook can be cleaned into a passing one."""
        bad_hook = "BREAKING: **r/gaming** just discovered something wild"
        result = validator.validate(bad_hook)
        assert not result.passed

        cleaned = validator.clean(bad_hook)
        # Cleaned version should at least remove the formatting artifacts
        assert "**" not in cleaned
        assert "BREAKING:" not in cleaned
        assert "r/gaming" not in cleaned

    def test_all_issues_combined(self, validator):
        """Multiple failures are all reported."""
        result = validator.validate("BREAKING: r/gaming **found** it")
        assert not result.passed
        assert len(result.all_issues) >= 2
