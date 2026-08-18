"""NARR-01 narration-script validator.

Enforcement lives in code (this module), not the LLM prompt. Every rule
maps to a named ``narration_degraded_reason`` slug that the consumer
persists on rejection.

Rules (all must pass; short-circuit on first failure):
  1. ``not_empty``            — text has ≥ 20 non-whitespace chars
  2. ``duration_fits``        — projected TTS duration fits the clip
                                with tail buffer
  3. ``no_urls``              — no http(s)://
  4. ``no_affiliate_ctas``    — no known affiliate-marker phrases
  5. ``no_first_person_experience_claims`` — no unsupported "I played /
                                watched / tried / tested / built /
                                created" claims

Returns ``(ok, reason_slug)`` where ``reason_slug`` is one of:
  ``''``                              — passed
  ``'script_generation_failed'``       — empty / whitespace-only
  ``'script_too_long'``                — projected duration overruns
  ``'script_contained_urls'``          — URL present
  ``'script_contained_affiliate_cta'`` — affiliate phrase present
  ``'script_first_person_claim'``      — unsupported first-person claim

Rationale for validator-in-code vs prompt-only:
  * LLM refusals + prompt-escapes are the rule not the exception
  * Prompt tokens are budget-constrained; validator has no such limit
  * Every rule has a named slug so the operator can query the DB for
    which rule is firing most (see plan section 4)
"""
from __future__ import annotations

import re
from typing import Final

# ── Constants ─────────────────────────────────────────────────────

_MIN_SCRIPT_CHARS: Final[int] = 20

# Compiled once at import; case-insensitive.
_URL_PATTERN: Final[re.Pattern[str]] = re.compile(r"https?://", re.IGNORECASE)

# Affiliate CTA markers. Substring match (case-insensitive) so
# "shop now" catches "SHOP NOW" and "Shop now — link in bio".
# The list is deliberately conservative — false positives here just
# mean an occasional narration_script gets rejected and the reel
# publishes without VO (legacy path). Better than shipping "Shop
# our GPU deals!" in a channel voiceover.
_AFFILIATE_MARKERS: Final[tuple[str, ...]] = (
    "shop now",
    "buy now",
    "grab yours",
    "link in bio",
    "swipe up",
    "affiliate",
    "discount code",
    "promo code",
    "coupon code",
    "use code ",
    "our sponsor",
    "sponsored by",
    "sale ends",
    "limited time",
    "check out our",
    "check out my",
    "get yours at",
)

# First-person experience claims. The channel is a curator/commentator
# — never claims to have personally played/watched/built the source
# content. This preserves editorial credibility + reduces the
# "AI-generated fake creator" signal on Meta's inauthentic-content
# detection.
_FIRST_PERSON_PATTERNS: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(rf"\bi\s+{verb}\b", re.IGNORECASE)
    for verb in (
        "played",
        "watched",
        "tried",
        "tested",
        "built",
        "created",
        "made",
        "designed",
        "coded",
        "wrote",
        "produced",
    )
)


# ── Public API ────────────────────────────────────────────────────


def project_tts_duration_seconds(text: str, wpm: int = 150) -> float:
    """Baseline projection: words × 60 / wpm. Used pre-synthesis for
    the fit gate. Post-synthesis, the pipeline probes actual duration
    with ffprobe (see A4 ``vo_overrun`` check) — this is the
    predictive gate, not the definitive one."""
    word_count = len(text.split())
    if wpm <= 0:
        wpm = 150
    return word_count * 60.0 / wpm


def validate_narration_script(
    text: str,
    clip_duration_seconds: float,
    wpm: int = 150,
    tail_buffer_seconds: float = 2.0,
) -> tuple[bool, str]:
    """Validate a candidate narration script.

    Args:
        text: the LLM-emitted narration text.
        clip_duration_seconds: length of the video clip the VO must fit.
        wpm: TTS speaking rate; matches ``narration.wpm`` niche config.
        tail_buffer_seconds: seconds at the end of the clip that the
            music bed carries alone (VO must not extend into this).

    Returns:
        (True, "") on pass. (False, "<reason_slug>") on any failure.

    Short-circuits on first rule failure — matches
    ``degradation_reasons`` enum in the NARR-01 plan.
    """
    # Rule 1: non-empty
    stripped = (text or "").strip()
    if len(stripped) < _MIN_SCRIPT_CHARS:
        return False, "script_generation_failed"

    # Rule 2: duration fits (predictive)
    projected_seconds = project_tts_duration_seconds(stripped, wpm)
    fit_budget = clip_duration_seconds - tail_buffer_seconds
    if projected_seconds > fit_budget:
        return False, "script_too_long"

    # Rule 3: no URLs
    if _URL_PATTERN.search(stripped):
        return False, "script_contained_urls"

    # Rule 4: no affiliate CTAs
    lower = stripped.lower()
    for marker in _AFFILIATE_MARKERS:
        if marker in lower:
            return False, "script_contained_affiliate_cta"

    # Rule 5: no first-person experience claims
    for pattern in _FIRST_PERSON_PATTERNS:
        if pattern.search(stripped):
            return False, "script_first_person_claim"

    return True, ""


__all__ = [
    "validate_narration_script",
    "project_tts_duration_seconds",
]
