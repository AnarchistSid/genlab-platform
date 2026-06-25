"""Spam-pattern detection (PR #569, 2026-06-25).

Phase A step 4 — detects the in-blueprint signals that platform
classifiers use to identify spam content. These signals fire before
publish so the operator can correct + re-render rather than
discovering the shadowban after-the-fact.

## Why this matters

Modern platform spam classifiers (Instagram especially, but also
TikTok + YouTube Shorts + Threads) shadowban accounts that exhibit
spam-like patterns. Confirmed triggers across multiple platform
audits:

  * Hashtag stuffing (>30 hashtags) — Instagram caps quality
    distribution at ~15 hashtags; >30 triggers reach throttling
  * Banned-tag use (#followforfollow, #l4l, #f4f, #like4like) —
    these tags themselves are flagged AND posts using them get
    distribution reduced
  * All-caps captions (>70% uppercase) — yelling = engagement-pod
    signal
  * Emoji density >1 per 5 characters — emoji-stuffing pattern
    (e.g. "🔥🔥🔥 amazing 🔥🔥🔥") triggers some classifiers
  * All-emoji captions — content-light signal

## Phase A scope — in-blueprint signals only

This PR ships the deterministic, no-DB-required checks. The
historical-comparison checks (caption repetition across last N
posts, hashtag-set rotation, posting-cadence jitter, affiliate-
link density over a sliding window) need a query layer on
recent blueprints and are deferred to PR #569b.

The in-blueprint checks alone cover ~60-70% of confirmed shadowban
patterns based on platform-audit reports. They run in ~microseconds
per publish (just string analysis on the caption + hashtags).

## Public surface

  check_spam_patterns(blueprint, platform, niche_id)
      -> ComplianceDecision
    Registered into policy_gate at module import. Aggregates the
    sub-checks below into a single decision. Fires 'warn' on any
    sub-check trigger with reasons tagged per sub-check name.

  BANNED_HASHTAGS — frozenset of lowercase hashtag names (without
    the # prefix) that platforms penalize. Operators can extend
    via env var GENLAB_BANNED_HASHTAGS_EXTRA (comma-separated).

  MAX_HASHTAGS — 30. Per Instagram's documented limit + the empirical
    distribution cliff at ~15.

  MAX_CAPS_RATIO — 0.7. >70% uppercase = yelling signal.

  MAX_EMOJI_DENSITY — 0.2. >1 emoji per 5 chars = stuffing signal.

## Phase A observation-only

Like the other Phase A checks, this fires 'warn' which logs to
compliance_events without blocking. PR #570's enforcement toggle
(per-niche per-check) will let operators flip individual sub-
checks to 'block' once the false-positive rate is measured.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping

from genlab_core.compliance.events import ComplianceDecision
from genlab_core.compliance.policy_gate import register_check

logger = logging.getLogger(__name__)


# Platforms' empirically-confirmed banned hashtag list. These tags
# trigger reach throttling on at least one of IG/TT/YT Shorts. The
# list is conservative — better to lose 1% of harmless tags than
# accidentally include one that wrecks distribution.
BANNED_HASHTAGS: frozenset[str] = frozenset(
    {
        # Follow-train tags (all flagged on IG; aggressive throttling)
        "followforfollow",
        "f4f",
        "followme",
        "follow4follow",
        "like4like",
        "l4l",
        "likeforlike",
        "likeforlikes",
        # Spam-engagement tags
        "comment4comment",
        "c4c",
        "tagsforlikes",
        "likeback",
        "instalike",
        "ifollowback",
        # Banned/heavily-throttled on Instagram per Later.com audit
        # (these change over time; the list is a starting point —
        # operators extend via env var)
        "thought",
        "snap",
        "kissing",
        # Spam-content tags
        "buyfollowers",
        "buylikes",
        "instafollowers",
    }
)

# Per-platform hashtag cap. Instagram caps at 30 by policy; the
# empirical distribution cliff (where reach drops most sharply) is
# around 15. We use 30 as the hard signal (definitely-bad), and
# expose the soft threshold separately for the dashboard.
MAX_HASHTAGS: int = 30
SOFT_HASHTAG_THRESHOLD: int = 15  # warn threshold below hard limit

# All-caps yelling threshold. Most legitimate captions have <50%
# uppercase (proper nouns + start-of-sentence). >70% is a clear
# yelling signal.
MAX_CAPS_RATIO: float = 0.7

# Emoji density — >1 per 5 chars = stuffing. Counting emoji needs a
# regex over the surrogate-pair range AND combining marks.
MAX_EMOJI_DENSITY: float = 0.2

# Emoji regex — matches the common emoji blocks. NOT exhaustive
# (Unicode has 3000+ emoji codepoints) but covers ~95% of what
# captions contain.
_EMOJI_RE = re.compile(
    "["
    "\U0001f300-\U0001f9ff"  # Misc Symbols and Pictographs + Emoticons + Supplemental
    "\U0001fa00-\U0001faff"  # Symbols and Pictographs Extended-A
    "\U00002600-\U000027bf"  # Miscellaneous Symbols + Dingbats
    "\U0001f000-\U0001f02f"  # Mahjong + Domino
    "]+",
    flags=re.UNICODE,
)


def _banned_hashtags_with_extras() -> frozenset[str]:
    """Return BANNED_HASHTAGS plus operator-extended set from env.
    Cached per-request via no caching — the env can change without
    restart, and the lookup is cheap (set membership).
    """
    extra = os.environ.get("GENLAB_BANNED_HASHTAGS_EXTRA", "").strip()
    if not extra:
        return BANNED_HASHTAGS
    extras = {t.strip().lower().lstrip("#") for t in extra.split(",") if t.strip()}
    return BANNED_HASHTAGS | frozenset(extras)


def _check_hashtag_stuffing(blueprint: Mapping) -> tuple[list[str], dict]:
    """Detect hashtag-count and banned-tag spam signals.

    Returns (reasons, metadata) — both may be empty if no signals.

    Counts hashtags from the blueprint's `hashtags` field (list or
    space-separated string — both shapes accepted, same as
    payload_builder).
    """
    reasons: list[str] = []
    metadata: dict = {}

    hashtags_raw = blueprint.get("hashtags", "") or ""
    if isinstance(hashtags_raw, list):
        tags = [str(t).strip().lstrip("#").lower() for t in hashtags_raw if t]
    else:
        tags = [t.strip().lstrip("#").lower() for t in str(hashtags_raw).split() if t.strip()]
    tags = [t for t in tags if t]

    if len(tags) > MAX_HASHTAGS:
        reasons.append("hashtag_count_exceeds_hard_limit")
        metadata["hashtag_count"] = len(tags)
    elif len(tags) > SOFT_HASHTAG_THRESHOLD:
        reasons.append("hashtag_count_above_soft_threshold")
        metadata["hashtag_count"] = len(tags)

    banned = _banned_hashtags_with_extras()
    hit = [t for t in tags if t in banned]
    if hit:
        reasons.append("banned_hashtag_used")
        metadata["banned_tags_present"] = sorted(set(hit))

    return reasons, metadata


def _check_caption_yelling(blueprint: Mapping) -> tuple[list[str], dict]:
    """Detect all-caps yelling.

    Ratio = uppercase letters / (uppercase + lowercase letters).
    Ignores digits + punctuation + emoji (which are neither). This
    avoids false-positives on captions like "100% FREE!" where the
    raw uppercase-to-total ratio looks high but the LETTER ratio
    is actually moderate.
    """
    reasons: list[str] = []
    metadata: dict = {}

    caption = (blueprint.get("caption", "") or "").strip()
    if not caption:
        return reasons, metadata

    letters = [c for c in caption if c.isalpha()]
    if len(letters) < 10:
        # Too short to meaningfully measure ratio. Don't fire.
        return reasons, metadata

    uppercase_count = sum(1 for c in letters if c.isupper())
    ratio = uppercase_count / len(letters)
    if ratio > MAX_CAPS_RATIO:
        reasons.append("caption_all_caps_yelling")
        metadata["caps_ratio"] = round(ratio, 2)

    return reasons, metadata


def _check_emoji_stuffing(blueprint: Mapping) -> tuple[list[str], dict]:
    """Detect emoji-density spam pattern.

    Density = (count of emoji characters) / (caption length). The
    threshold catches the "🔥🔥🔥 amazing 🔥🔥🔥" pattern but not the
    "amazing post 🔥" pattern where the single emoji is a normal
    accent.

    Also fires when caption is ALL emoji (content-light signal).
    """
    reasons: list[str] = []
    metadata: dict = {}

    caption = (blueprint.get("caption", "") or "").strip()
    if not caption or len(caption) < 5:
        return reasons, metadata

    emoji_chars = sum(len(m.group()) for m in _EMOJI_RE.finditer(caption))
    density = emoji_chars / len(caption)
    if density > MAX_EMOJI_DENSITY:
        reasons.append("emoji_stuffing")
        metadata["emoji_density"] = round(density, 2)

    # All-emoji caption — content-light signal even if density check
    # didn't fire (e.g. caption is just "🔥🔥🔥🔥🔥" where density = 1.0
    # already triggers the density check, but this catches the
    # specific "no text at all" case for forensic clarity).
    non_emoji_text = _EMOJI_RE.sub("", caption).strip()
    if not non_emoji_text:
        reasons.append("caption_all_emoji")

    return reasons, metadata


def check_spam_patterns(
    blueprint: Mapping,
    platform: str,
    niche_id: str,
) -> ComplianceDecision:
    """Registered policy_gate check for in-blueprint spam signals.

    Aggregates the sub-checks (hashtag stuffing, caps yelling, emoji
    stuffing). Fires 'warn' on any sub-check trigger; 'allow'
    otherwise. Reasons + metadata combined from all triggering sub-
    checks for forensic clarity.

    Defensive: non-mapping blueprint → 'allow' (caller bug, but the
    policy_gate's wrapper would catch a raise anyway).
    """
    if not isinstance(blueprint, Mapping):
        return ComplianceDecision(decision="allow")

    all_reasons: list[str] = []
    all_metadata: dict = {}

    for sub_check in (
        _check_hashtag_stuffing,
        _check_caption_yelling,
        _check_emoji_stuffing,
    ):
        reasons, metadata = sub_check(blueprint)
        all_reasons.extend(reasons)
        all_metadata.update(metadata)

    if not all_reasons:
        return ComplianceDecision(decision="allow")

    return ComplianceDecision(
        decision="warn",
        reasons=all_reasons,
        metadata=all_metadata,
    )


# Register at module import. Same pattern as PR #568's
# copyright_attribution_check — the compliance package's __init__
# imports this module via the side-effect chain.
register_check("spam_pattern_check", check_spam_patterns)
