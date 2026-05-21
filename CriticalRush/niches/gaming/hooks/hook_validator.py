"""Gaming-specific hook validator.

Wraps the universal genlab_core HookValidator (9 format/safety rules) and adds
gaming-specific content rules:

  1. Reddit phrase leakage (r/gaming, /u/username, TIL, ELI5)
  2. Formal opener detection ("We are pleased", "It has been announced")
  3. Weak opener detection ("So", "Well", "Okay so")
  4. Markdown artifact cleaning (strips **, *, __, #)

Usage:
    from niches.gaming.hooks.hook_validator import GamingHookValidator

    validator = GamingHookValidator()
    result = validator.validate("**BREAKING:** r/gaming just found out...")
    if not result.passed:
        cleaned = validator.clean(result.hook)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from genlab_core.intelligence.hook_validator import (
    HookFailure,
    HookValidator,
)

MAX_HOOK_LENGTH = 60
MIN_HOOK_LENGTH = 20

# Reddit phrases that leak from source scraping
_REDDIT_PHRASES = re.compile(
    r"\br/\w+"
    r"|\b/u/\w+"
    r"|\bTIL\b"
    r"|\bELI5\b"
    r"|\bIMO\b"
    r"|\bIIRC\b"
    r"|\[OC\]"
    r"|\[Spoiler\]"
    r"|\[Discussion\]",
    re.IGNORECASE,
)

# Formal openers that break the energetic brand voice
_FORMAL_OPENERS = re.compile(
    r"^(?:We are pleased|It has been announced|"
    r"In a recent development|According to sources|"
    r"The company has announced|It is worth noting|"
    r"Ladies and gentlemen|Dear gamers)",
    re.IGNORECASE,
)

# Weak openers that waste scroll-stopping real estate
_WEAK_OPENERS = re.compile(
    r"^(?:So |Well |Okay so |Um |Uh |Like |Basically |Honestly |"
    r"I mean |You know |Look |Listen )",
    re.IGNORECASE,
)

# Markdown patterns to strip during cleaning
_MARKDOWN_STRIP = re.compile(r"\*\*|__|[*_]|^#{1,6}\s+", re.MULTILINE)

# News-anchor prefixes forbidden in gaming brand voice
_NEWS_PREFIXES = re.compile(
    r"^(?:BREAKING:|JUST IN:|ALERT:|URGENT:|EXCLUSIVE:)\s*",
    re.IGNORECASE,
)


class GamingHookFailure:
    """Gaming-specific failure reasons (extend the universal HookFailure enum)."""

    REDDIT_PHRASE = "gaming_reddit_phrase"
    FORMAL_OPENER = "gaming_formal_opener"
    WEAK_OPENER = "gaming_weak_opener"
    NEWS_PREFIX = "gaming_news_prefix"
    TOO_LONG = "gaming_too_long"
    TOO_SHORT = "gaming_too_short"


@dataclass
class GamingHookResult:
    """Combined result from universal + gaming-specific validation."""

    hook: str
    passed: bool = True
    universal_failures: list[HookFailure] = field(default_factory=list)
    gaming_failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def all_issues(self) -> list[str]:
        """All failure reasons as strings."""
        return [f.value for f in self.universal_failures] + self.gaming_failures


class GamingHookValidator:
    """Validate hooks against universal rules + gaming-specific content rules."""

    def __init__(self) -> None:
        self._universal = HookValidator()

    def validate(self, hook: str, platform: str = "instagram") -> GamingHookResult:
        """Run all universal + gaming-specific checks."""
        result = GamingHookResult(hook=hook)

        # Universal rules first
        universal = self._universal.validate(hook, platform)
        if not universal.passed:
            result.universal_failures = universal.failures

        # Gaming-specific rules
        self._check_reddit_phrases(hook, result)
        self._check_formal_opener(hook, result)
        self._check_weak_opener(hook, result)
        self._check_news_prefix(hook, result)
        self._check_gaming_length(hook, result)

        result.passed = len(result.universal_failures) == 0 and len(result.gaming_failures) == 0
        return result

    def clean(self, hook: str) -> str:
        """Strip common artifacts to salvage a failing hook.

        Applies cleaning in order:
          1. Strip markdown formatting
          2. Remove news-anchor prefixes
          3. Remove weak openers
          4. Strip Reddit phrases
          5. Collapse whitespace
        """
        cleaned = _MARKDOWN_STRIP.sub("", hook)
        cleaned = _NEWS_PREFIXES.sub("", cleaned)
        cleaned = _WEAK_OPENERS.sub("", cleaned)
        cleaned = _REDDIT_PHRASES.sub("", cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
        return cleaned

    # -- Private checks --

    def _check_reddit_phrases(self, hook: str, result: GamingHookResult) -> None:
        if _REDDIT_PHRASES.search(hook):
            result.gaming_failures.append(GamingHookFailure.REDDIT_PHRASE)

    def _check_formal_opener(self, hook: str, result: GamingHookResult) -> None:
        if _FORMAL_OPENERS.match(hook):
            result.gaming_failures.append(GamingHookFailure.FORMAL_OPENER)

    def _check_weak_opener(self, hook: str, result: GamingHookResult) -> None:
        if _WEAK_OPENERS.match(hook):
            result.gaming_failures.append(GamingHookFailure.WEAK_OPENER)

    def _check_news_prefix(self, hook: str, result: GamingHookResult) -> None:
        if _NEWS_PREFIXES.match(hook):
            result.gaming_failures.append(GamingHookFailure.NEWS_PREFIX)

    def _check_gaming_length(self, hook: str, result: GamingHookResult) -> None:
        if len(hook) > MAX_HOOK_LENGTH:
            result.gaming_failures.append(GamingHookFailure.TOO_LONG)
        elif len(hook) < MIN_HOOK_LENGTH:
            result.gaming_failures.append(GamingHookFailure.TOO_SHORT)
