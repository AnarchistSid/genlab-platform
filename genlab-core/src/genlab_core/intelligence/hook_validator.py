"""genlab_core.intelligence.hook_validator — Universal hook quality validation.

The 9 universal rules validate hook format and platform safety.
They are niche-agnostic: a hook that fails these rules will underperform
on any platform regardless of topic.

What belongs here (universal, form-based):
  1. Too short (<3 words)
  2. Too long (exceeds platform character limit)
  3. Markdown artifacts (**bold**, *italic*, __underline__, # headers)
  4. Reddit formatting (/r/, /u/, ^superscript, [flair])
  5. Excessive punctuation (!!!, ???)
  6. Incomplete sentence (trailing ..., dangling dash, stopword ending)
  7. Contains URL
  8. Emoji spam (>7 emojis in hook text)
  9. Mixed CAPS/lowercase (broken template artifacts)

Extracted from Content Scraper's generate_hooks.py validate_hook_grammar().

What does NOT belong here (niche-specific, content-based):
  - Whether the hook matches the brand voice
  - Whether the hook uses the correct gaming vocabulary
  - Whether the hook tone is appropriate for the niche audience
  Those stay in niches/*/strategies/HookStrategy.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class HookFailure(Enum):
    TOO_SHORT = "hook_too_short"
    TOO_LONG = "hook_too_long"
    MARKDOWN_ARTIFACTS = "markdown_artifacts"
    REDDIT_FORMATTING = "reddit_formatting"
    EXCESSIVE_PUNCT = "excessive_punctuation"
    INCOMPLETE_SENTENCE = "incomplete_sentence"
    CONTAINS_URL = "contains_url"
    EMOJI_SPAM = "emoji_spam"
    MIXED_CAPS = "mixed_caps_lowercase"


@dataclass
class HookValidationResult:
    """Result of validating a single hook."""

    hook: str
    platform: str
    passed: bool = True
    failures: list[HookFailure] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# Regex for detecting emoji characters (covers most common ranges)
_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # misc symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map symbols
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U00002702-\U000027B0"  # dingbats
    "\U0001F900-\U0001F9FF"  # supplemental symbols
    "\U0001FA00-\U0001FA6F"  # chess symbols
    "\U0001FA70-\U0001FAFF"  # symbols extended
    "\U00002600-\U000026FF"  # misc symbols
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "\U0000200D"  # ZWJ
    "\U00002B50"  # star
    "\U00002B55"  # circle
    "\U000023F0-\U000023FA"  # clocks
    "\U0000203C-\U00003299"  # misc
    "]+",
    flags=re.UNICODE,
)

# Markdown patterns: **bold**, *italic*, __underline__, # headers, > blockquotes
_MARKDOWN_RE = re.compile(
    r"\*\*[^*]+\*\*"  # **bold**
    r"|\*[^*]+\*"  # *italic*
    r"|__[^_]+__"  # __underline__
    r"|^#{1,6}\s"  # # headers
    r"|^>\s",  # > blockquotes
    re.MULTILINE,
)

# Reddit formatting patterns
_REDDIT_RE = re.compile(
    r"/r/\w+"  # subreddit references
    r"|/u/\w+"  # user references
    r"|\^[\w(]"  # ^superscript
    r"|\[[\w\s]+\]\s*$",  # [flair] tags at end
)

# URL pattern
_URL_RE = re.compile(
    r"https?://\S+"
    r"|www\.\S+"
    r"|\w+\.(?:com|org|net|io|co|dev|app|ai)\b/?\S*",
)

# Known ALL-CAPS patterns that are intentional (not grammatical errors)
# Sourced from CS's generate_hooks.py _INTENTIONAL_CAPS set
_INTENTIONAL_CAPS = {
    "BREAKING",
    "JUST IN",
    "URGENT",
    "MASSIVE",
    "WATCH",
    "UPDATE",
    "AI",
    "GPT",
    "LLM",
    "CEO",
    "CTO",
    "API",
    "UI",
    "UX",
    "AGI",
    "USA",
    "EU",
    "UK",
    "VR",
    "AR",
    "XR",
    "SDK",
    "GG",
    "OP",
    "MVP",
    "DLC",
    "FPS",
    "RPG",
    "MMO",
    "PVP",
    "PVE",
    "RNG",
    "AFK",
    "NPC",
}


class HookValidator:
    """Universal hook quality validator.

    Run this BEFORE any niche-specific hook strategy so that invalid hooks
    never reach the publish stage. CriticalRush currently skips all validation,
    meaning hooks with markdown artifacts and Reddit formatting are published daily.

    Usage:
        validator = HookValidator()
        result = validator.validate(
            hook="**INSANE** play in ranked r/leagueoflegends!!!",
            platform="instagram",
        )
        if not result.passed:
            for failure in result.failures:
                logger.warning("Hook failed: %s", failure.value)
    """

    # Platform character limits for the hook/title
    _TITLE_LIMITS: dict[str, int] = {
        "youtube": 100,
        "instagram": 2200,
        "tiktok": 2200,
        "twitter": 280,
        "x": 280,
        "facebook": 500,
        "threads": 500,
    }

    def validate(self, hook: str, platform: str) -> HookValidationResult:
        """Validate a hook against all 9 universal rules.

        Args:
            hook:     The hook text string to validate.
            platform: Target platform (lowercase). Used for character limit check.

        Returns:
            HookValidationResult. Check .passed before using the hook.
        """
        result = HookValidationResult(hook=hook, platform=platform)
        self._check_length(hook, platform, result)
        self._check_markdown_artifacts(hook, result)
        self._check_reddit_formatting(hook, result)
        self._check_excessive_punctuation(hook, result)
        self._check_incomplete_sentence(hook, result)
        self._check_url_presence(hook, result)
        self._check_emoji_density(hook, result)
        self._check_mixed_caps(hook, result)

        result.passed = len(result.failures) == 0
        return result

    def validate_batch(
        self,
        hooks: list[str],
        platform: str,
    ) -> list[HookValidationResult]:
        """Validate a list of hooks. Returns results in same order."""
        return [self.validate(hook, platform) for hook in hooks]

    def filter_valid(
        self,
        hooks: list[str],
        platform: str,
    ) -> list[str]:
        """Return only the hooks that pass all validations."""
        return [h for h in hooks if self.validate(h, platform).passed]

    # -- Private validation methods (logic extracted from CS generate_hooks.py) --

    def _check_length(
        self, hook: str, platform: str, result: HookValidationResult
    ) -> None:
        """Rule 1 & 2: Too short (<3 words) or too long (platform limit)."""
        words = hook.split()
        if len(words) < 3:
            result.failures.append(HookFailure.TOO_SHORT)

        limit = self._TITLE_LIMITS.get(platform.lower())
        if limit and len(hook) > limit:
            result.failures.append(HookFailure.TOO_LONG)

    def _check_markdown_artifacts(
        self, hook: str, result: HookValidationResult
    ) -> None:
        """Rule 3: Markdown bold/italic/headers render as literal chars on social."""
        if _MARKDOWN_RE.search(hook):
            result.failures.append(HookFailure.MARKDOWN_ARTIFACTS)

    def _check_reddit_formatting(
        self, hook: str, result: HookValidationResult
    ) -> None:
        """Rule 4: Reddit /r/, /u/, ^superscript, [flair] patterns."""
        if _REDDIT_RE.search(hook):
            result.failures.append(HookFailure.REDDIT_FORMATTING)

    def _check_excessive_punctuation(
        self, hook: str, result: HookValidationResult
    ) -> None:
        """Rule 5: Multiple ! or ? in sequence (3+) looks like spam."""
        if re.search(r"[!]{3,}|[?]{3,}", hook):
            result.failures.append(HookFailure.EXCESSIVE_PUNCT)

    def _check_incomplete_sentence(
        self, hook: str, result: HookValidationResult
    ) -> None:
        """Rule 6: Trailing ..., dangling dash, stopword ending, or colon.

        Extracted from CS's validate_hook_grammar rules 6 and 9:
        - Ends with colon (truncated template)
        - Ends with dangling dash
        - Ends with a stopword (and, or, for, to, in, with, of, etc.)
        """
        stripped = hook.rstrip()

        # Trailing ellipsis
        if stripped.endswith("..."):
            result.failures.append(HookFailure.INCOMPLETE_SENTENCE)
            return

        # Ends with colon (truncated template)
        if stripped.endswith(":"):
            result.failures.append(HookFailure.INCOMPLETE_SENTENCE)
            return

        # Dangling dash
        if re.search(r"[-\u2014\u2013]\s*$", stripped):
            result.failures.append(HookFailure.INCOMPLETE_SENTENCE)
            return

        # Ends with dangling stopword
        words = stripped.split()
        if words:
            tail = words[-1].lower().strip(".,;:!?—-")
            _DANGLING = {
                "and", "or", "for", "to", "in", "with", "of", "at",
                "by", "on", "the", "a", "an",
            }
            if tail in _DANGLING:
                result.failures.append(HookFailure.INCOMPLETE_SENTENCE)

    def _check_url_presence(
        self, hook: str, result: HookValidationResult
    ) -> None:
        """Rule 7: URLs belong in platform-specific fields, not the hook text."""
        if _URL_RE.search(hook):
            result.failures.append(HookFailure.CONTAINS_URL)

    def _check_emoji_density(
        self, hook: str, result: HookValidationResult
    ) -> None:
        """Rule 8: >7 emoji sequences = engagement bait signal."""
        emoji_matches = _EMOJI_RE.findall(hook)
        if len(emoji_matches) > 7:
            result.failures.append(HookFailure.EMOJI_SPAM)

    def _check_mixed_caps(
        self, hook: str, result: HookValidationResult
    ) -> None:
        """Rule 9: Mixed ALL-CAPS and lowercase = broken template artifact.

        From CS's validate_hook_grammar rule 5: if >30% of alpha words are
        ALL-CAPS and >20% are all-lowercase in a hook >=4 words, it's a
        broken template substitution pattern.
        """
        words = hook.split()
        if len(words) < 4:
            return

        alpha_words = [w for w in words if w.isalpha() and len(w) > 1]
        if not alpha_words:
            return

        caps_words = [
            w for w in alpha_words
            if w.isupper() and w not in _INTENTIONAL_CAPS
        ]
        lower_words = [w for w in alpha_words if w.islower() and len(w) > 2]

        caps_ratio = len(caps_words) / len(alpha_words)
        lower_ratio = len(lower_words) / len(alpha_words)

        if caps_ratio > 0.3 and lower_ratio > 0.2:
            result.failures.append(HookFailure.MIXED_CAPS)
