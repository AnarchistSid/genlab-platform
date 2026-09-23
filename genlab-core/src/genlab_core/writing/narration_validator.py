"""NARR-01 narration-script validator.

Enforcement lives in code (this module), not the LLM prompt. Every rule
maps to a named ``narration_degraded_reason`` slug that the consumer
persists on rejection.

Rules (all must pass; short-circuit on first failure):
  1. ``not_empty``            — text has ≥ 20 non-whitespace chars
  2. ``fills_the_reel``       — projected duration is at least
                                ``min_fraction`` of the budget (OFF by
                                default; ANIME-15 §6 turns it on)
  3. ``duration_fits``        — projected TTS duration fits the clip
                                with tail buffer
  3. ``no_urls``              — no http(s)://
  4. ``no_affiliate_ctas``    — no known affiliate-marker phrases
  5. ``no_first_person_experience_claims`` — no unsupported "I played /
                                watched / tried / tested / built /
                                created" claims

Returns ``(ok, reason_slug)`` where ``reason_slug`` is one of:
  ``''``                              — passed
  ``'script_generation_failed'``       — empty / whitespace-only
  ``'script_too_short'``               — projected duration under-fills
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


def fit_budget_seconds(
    clip_duration_seconds: float, tail_buffer_seconds: float = 2.0,
    fit_margin: float = 0.0,
) -> float:
    """The seconds a narration may actually occupy. ONE definition."""
    budget = clip_duration_seconds - tail_buffer_seconds
    return budget * (1.0 - max(0.0, min(fit_margin, 0.9)))


def word_cap(clip_duration_seconds: float, wpm: int = 150,
             tail_buffer_seconds: float = 2.0, fit_margin: float = 0.0) -> int:
    """The largest word count that fits. What the prompt must state."""
    return int(fit_budget_seconds(clip_duration_seconds, tail_buffer_seconds,
                                  fit_margin) * max(wpm, 1) / 60)


def word_floor(clip_duration_seconds: float, wpm: int = 150,
               tail_buffer_seconds: float = 2.0, fit_margin: float = 0.0,
               min_fraction: float = 0.0) -> int:
    """The smallest word count that PASSES. What the prompt must state.

    Derived from the same seconds the validator checks, then rounded UP, so
    a script written to exactly this number is accepted. Computing it as
    ``int(word_cap * min_fraction)`` instead gave 64 where the validator
    required 65 — a script obeying the prompt, rejected. That is the NARR-11
    defect for a fourth time: one contract, two implementers, allowed to
    drift. Both callers now read this function.
    """
    if min_fraction <= 0.0:
        return 0
    import math

    seconds = fit_budget_seconds(clip_duration_seconds, tail_buffer_seconds,
                                 fit_margin) * min_fraction
    return max(1, math.ceil(seconds * max(wpm, 1) / 60))


def validate_narration_script(
    text: str,
    clip_duration_seconds: float,
    wpm: int = 150,
    tail_buffer_seconds: float = 2.0,
    fit_margin: float = 0.0,
    min_fraction: float = 0.0,
) -> tuple[bool, str]:
    """Validate a candidate narration script.

    Args:
        text: the LLM-emitted narration text.
        clip_duration_seconds: length of the video clip the VO must fit.
        wpm: TTS speaking rate; matches ``narration.wpm`` niche config.
        tail_buffer_seconds: seconds at the end of the clip that the
            music bed carries alone (VO must not extend into this).
        min_fraction: floor as a fraction of the fit budget. 0.0 disables
            it, which is the default so every pre-existing caller is
            unchanged. ANIME-15 §6 sets it to 0.70 for anime.

            This is the twin of ``script_too_long`` and it has been named in
            four packets without existing. TOUGEN ANKI's pipeline script is
            20 spoken words against a 60-word target: it passes every rule
            here, produces a correct reel, and that reel is 9 seconds long
            against a 15-second floor. Nothing downstream can fix it —
            stretching 20 words means holding stills longer, which the
            longest-static rule forbids.

    Returns:
        (True, "") on pass. (False, "<reason_slug>") on any failure.

    Short-circuits on first rule failure — matches
    ``degradation_reasons`` enum in the NARR-01 plan.
    """
    # Rule 1: non-empty
    stripped = (text or "").strip()
    if len(stripped) < _MIN_SCRIPT_CHARS:
        return False, "script_generation_failed"

    projected_seconds = project_tts_duration_seconds(stripped, wpm)
    fit_budget = clip_duration_seconds - tail_buffer_seconds
    # Both bounds read the SAME budget helper. Computing the floor against the
    # pre-margin budget while ``word_floor`` used the post-margin one put them
    # 4 words apart, so a script written to the stated minimum was rejected —
    # the same drift this fix was introduced to remove, one layer down.
    effective_budget = fit_budget_seconds(
        clip_duration_seconds, tail_buffer_seconds, fit_margin
    )

    # Rule 2: fills the reel. Checked BEFORE the upper bound because a script
    # can only be one of the two, and reporting the one that is actually
    # wrong matters for which correction the retry carries.
    if min_fraction > 0.0 and projected_seconds < effective_budget * min_fraction:
        return False, "script_too_short"

    # Rule 3: duration fits (predictive)
    # NARR-12: reject inside a safety margin, not just past the budget.
    # Projection is a model; measured TTS ran 6.6% slow on round 3. Default
    # 0.0 preserves the original behaviour for callers that don't opt in.
    if projected_seconds > effective_budget:
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
    "fit_budget_seconds",
    "word_cap",
    "word_floor",
    "validate_narration_script",
    "project_tts_duration_seconds",
]
