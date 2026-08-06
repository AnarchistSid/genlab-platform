"""Pipeline stage: Push stories and blueprints to SharePoint backlog.

Shared implementation for all niches. Reads ``niche_id`` from the
pipeline context instead of a hardcoded constant, eliminating the
need for per-niche copies of this stage.

Usage:
    stage = PushToBacklog()
    context = stage.execute(context)   # context["niche_id"] must be set
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC
from pathlib import Path
from typing import Any

from genlab_core.cache.stable_ids import generate_candidate_id, generate_story_id
from genlab_core.cache.text_sanitizer import strip_html_tags
from genlab_core.http.backlog_client import BacklogClient
from genlab_core.pipeline.stage_context import StageContext
from genlab_core.pipeline.video_id_dedup import VideoIdDedupGate
from genlab_core.pipeline.video_id_dedup import is_blocking as _is_blocking
from genlab_core.settings import settings
from genlab_core.utils.text_sanitizer import sanitize_for_graph_api

logger = logging.getLogger(__name__)

# R-53: text fields that ship to platforms / Postgres and must be HTML-stripped.
# LLM output can carry <cite>/<p>/<strong> tags; the Graph sanitizer runs on the
# SharePoint path but is bypassed on the Postgres path, so strip here — the one
# chokepoint every niche and every writer passes through.
_HTML_STRIP_FIELDS = (
    "hook",
    "hook_text",
    "caption",
    "youtube_content",
    "facebook_content",
    "threads_content",
)

# Default arm mapping per niche — maps content signals to bandit arm IDs.
# These must match the arm_id values in the bandit_arms table.
_NICHE_ARM_DEFAULTS: dict[str, str] = {
    "gaming": "gameplay_clip",
    "sports": "highlight_play",
    "movies": "trailer_drop",
    "anime": "episode_moment",
    "ai_creators": "tool_demo",
}

# 2026-07-14 keyword coverage expansion: measured on 100 recent production
# hooks/titles/summaries, V1 keywords produced 67-88% no-match rates across
# niches → propensity=None → excluded from doubly-robust replay. Only 3 of
# 939 pending_feedback rows had usable propensity. V2 expansion (below) +
# a new ``viral_moment`` arm per non-ai_creators niche lifted measured
# coverage to 41-78% across the same 100-row sample. See PR notes /
# docs/agent-vision-2026-07-10.md capability #3 for the DR-replay wire
# rationale.
#
# Ship discipline: additions only broaden existing arms' semantic reach —
# no removals from V1 lists. viral_moment is a NEW arm; arm_loader auto-
# initialises it at first-touch with Beta(1,1) prior + any cross-niche
# transferred prior available. Cold-start rate slightly increases the
# first week until viral_moment accumulates n_obs≥3 per niche; after that,
# multi-match cases involving viral_moment start producing LinUCB softmax
# propensities (currently 0 multi-match rows exist for anime/gaming/
# movies/sports because their V1 arm keyword sets were disjoint).
_ARM_KEYWORDS: dict[str, list[tuple[str, list[str]]]] = {
    "gaming": [
        (
            "esports_highlight",
            [
                "esports",
                "tournament",
                "championship",
                "league",
                "competitive",
                "clutch",
                "pro player",
                "match",
            ],
        ),
        (
            "trailer_reaction",
            [
                "trailer",
                "reveal",
                "announcement",
                "launch",
                "release",
                "gameplay",
                "showcase",
                "demo",
            ],
        ),
        (
            "patch_news",
            [
                "patch",
                "update",
                "nerf",
                "buff",
                "season",
                "hotfix",
                "tier",
                "meta",
                "balance",
            ],
        ),
        # NEW 2026-07-14: viral trending catches "hit Twitch top 3", "went
        # viral", "everyone's playing X" — dominant pattern in production
        # gaming hooks that had zero V1 arm match.
        (
            "viral_moment",
            [
                "viral",
                "trending",
                "twitch",
                "streamed",
                "streaming",
                "millions",
                "broke",
                "top 1",
                "top 3",
                "#1",
                "everyone",
                "hit",
                "biggest",
            ],
        ),
    ],
    "sports": [
        (
            "breaking_trade",
            [
                "trade",
                "transfer",
                "sign",
                "contract",
                "deal",
                "free agent",
                "signing",
                "traded",
                "acquired",
            ],
        ),
        (
            "record_milestone",
            [
                "record",
                "milestone",
                "history",
                "first ever",
                "youngest",
                "oldest",
                "greatest",
                "best",
                "worst",
                "all-time",
            ],
        ),
        (
            "upset_reaction",
            [
                "upset",
                "shock",
                "underdog",
                "eliminated",
                "comeback",
                "beat",
                "defeated",
                "loss",
                "stunning",
            ],
        ),
        (
            "viral_moment",
            [
                "viral",
                "trending",
                "clip",
                "highlight",
                "moment",
                "goal",
                "play",
                "buzzer",
                "walk-off",
                "insane",
                "unbelievable",
            ],
        ),
    ],
    "movies": [
        (
            "scene_reaction",
            [
                "scene",
                "moment",
                "clip",
                "reaction",
                "watch",
                "villain",
                "hero",
                "iconic",
                "backstory",
                "shot",
            ],
        ),
        (
            "box_office_update",
            [
                "box office",
                "opening weekend",
                "gross",
                "million",
                "billion",
                "flop",
                "record-breaking",
            ],
        ),
        (
            "cast_reveal",
            [
                "cast",
                "role",
                "playing",
                "starring",
                "joins",
                "reveal",
                "origin",
                "prequel",
                "spinoff",
                "sequel",
                "returns",
                "returning",
                "brings back",
                "back after",  # "bringing Woody back after 9 years" pattern
            ],
        ),
        (
            "viral_moment",
            [
                "viral",
                "trending",
                "millions",
                "broke",
                "everyone",
                "nobody",
                "shocking",
                "insane",
                "wild",
            ],
        ),
    ],
    "anime": [
        (
            "season_announcement",
            [
                "season",
                "announced",
                "confirmed",
                "adaptation",
                "premiere",
                "release date",
                "arc",
            ],
        ),
        (
            "fight_scene",
            [
                "fight",
                "battle",
                "vs",
                "clash",
                "power",
                "epic",
                "attack",
                "defeated",
                "beat",
                "showdown",
            ],
        ),
        (
            "adaptation_news",
            [
                "manga",
                "light novel",
                "adaptation",
                "studio",
                "animated",
                "chapter",
                "volume",
                "novel",
            ],
        ),
        (
            "viral_moment",
            [
                "viral",
                "trending",
                "edit",
                "shorts",
                "millions of views",
                "broke",
                "everyone",
                "iconic",
                "moment",
                "scene",
            ],
        ),
    ],
    "ai_creators": [
        (
            "model_release",
            [
                "release",
                "launched",
                "model",
                "gpt",
                "claude",
                "gemini",
                "llm",
                "openai",
                "anthropic",
                "google",
                "meta ai",
                "xai",
                "grok",
                "hardware",
                "chip",
                "api",
            ],
        ),
        (
            "creative_showcase",
            [
                "created",
                "made",
                "generated",
                "art",
                "music",
                "video",
                "film",
                "built",
                "developer",
                "coded",
                "app",
                "tool",
                "prompt",
            ],
        ),
        (
            "comparison_test",
            [
                "vs",
                "compared",
                "better",
                "benchmark",
                "test",
                "which",
                "beats",
                "outperforms",
                "ranked",
                "faster",
                "smarter",
                "wins",
            ],
        ),
    ],
}


# `_BLOCKING_STATUSES` is now imported at the top of the file from
# :mod:`genlab_core.pipeline.blueprint_status` (the alias keeps existing
# internal references working). Anything in this set means we've already
# committed to producing or shipping that content; ARCHIVED / PUBLISH_FAILED
# stay outside it because retrying those is the whole point of the pipeline.
# PreDownloadDedup uses the narrower ``LIVE`` subset of the same canonical
# enum — the subset relationship encodes the policy difference between the
# two stages.


# ``_is_blocking`` is imported at the top of this module from
# :mod:`genlab_core.pipeline.video_id_dedup` — single source of truth
# so the extracted :class:`VideoIdDedupGate` and the inline callers
# below (candidate_id dedup, existing_hooks loader) can't drift apart.
# The alias name is preserved to keep every existing test import
# (``from genlab_core.pipeline.stages.push_to_backlog import _is_blocking``)
# working without a test-file edit.


_TOPIC_MAP = {
    # Normalize raw source names to clean categories
    "anilist": "anilist",
    "jikan_promos": "jikan",
    "rss_anime_news_network": "anime_news",
    "rss_screen_rant": "screen_rant",
    "rss_ign_movies": "ign",
    "rss_ign": "ign",
    "rss_indiewire": "indiewire",
    "rss_deadline_hollywood": "deadline",
    "rss_variety": "variety",
    "rss_hollywood_reporter": "hollywood_reporter",
    "rss_collider": "collider",
    "rss_espn": "espn",
    "espn_news": "espn_news",
    "espn_scoreboard": "espn_live",
    "tmdb_trailer": "tmdb_trailer",
    "youtube_trending": "youtube_trending",
    "youtube_rss": "youtube_channel",
    "youtube_content": "youtube_content",
    "twitch_trending": "twitch_trending",
    "twitch_clip": "twitch_clip",
    "scorebat": "scorebat",
    "steam_trailer": "steam",
    "rss_fallback": "rss_fallback",
    "channel_subscription": "youtube_channel",
    "rss": "rss",
}


# Cross-niche source guard: per niche_id, sources matching any of these
# prefixes indicate a contamination leak (e.g. ai_creators producing
# tmdb_upcoming stories means BB ran SR stages). Stories matching are
# dropped before write with structured telemetry.
_FORBIDDEN_SOURCE_PREFIXES: dict[str, frozenset[str]] = {
    "ai_creators": frozenset(
        {
            "tmdb_",
            "scorebat",
            "espn",
            "sport_",
            "twitch_clip",
            "twitch_trending",
            "steam_",
            "igdb",
            "anilist",
            "jikan_",
            "anime_promo",
        }
    ),
    "gaming": frozenset(
        {
            "tmdb_",
            "scorebat",
            "espn",
            "sport_",
            "anilist",
            "jikan_",
            "anime_promo",
        }
    ),
    "sports": frozenset(
        {
            "tmdb_",
            "twitch_clip",
            "twitch_trending",
            "steam_",
            "igdb",
            "anilist",
            "jikan_",
            "anime_promo",
        }
    ),
    "movies": frozenset(
        {
            "scorebat",
            "espn",
            "sport_",
            "twitch_clip",
            "twitch_trending",
            "steam_",
            "igdb",
            "anilist",
            "jikan_",
            "anime_promo",
        }
    ),
    "anime": frozenset(
        {
            "tmdb_",
            "scorebat",
            "espn",
            "sport_",
            "twitch_clip",
            "twitch_trending",
            "steam_",
            "igdb",
        }
    ),
}


def _is_publishable(media: dict) -> bool:
    """R-47: a rendered clip is publishable (VISUAL_READY) only if video
    validation did not FAIL.

    Absent validation = "not checked" -> publishable if a render exists
    (preserves prior behavior, so the pipeline doesn't stall when the
    validate stage is disabled). An explicit ``{"valid": False}`` (VMAF /
    spec / no-audio failure) must stay DRAFTED and never be scheduled —
    previously the status was gated only on ``rendered_path`` and the
    validation result was ignored, so failed videos shipped.
    """
    media = media or {}
    if not media.get("rendered_path"):
        return False
    return (media.get("video_validation") or {}).get("valid", True) is not False


def _strip_captioned_when_whisper_disabled(
    rendered_path: str,
    niche_config: dict,
) -> str:
    """Defensive guard: if whisper_sync is disabled but ``rendered_path``
    ends in ``_captioned.mp4``, swap to the non-captioned sibling.

    Why this exists (2026-06-14): for ~10 days the dashboard served stale
    ``_captioned.mp4`` variants for blueprints rendered while whisper was
    enabled. The captioned variants have a regressed text-renderer bug
    (giant overlays — see ``[[session-2026-06-13-render-audit-findings]]``)
    that PR #177's ``text_optimizer`` revival fixes for NEW renders only.
    Once the operator disabled whisper, NEW captioned files stopped being
    produced, but the existing ones lingered in 7 blueprints' visual_paths.

    This guard catches the same class of bug going forward: even if a
    rendered_path arrives carrying ``_captioned``, we drop it when the
    current niche config says whisper is off. The non-captioned sibling
    must exist on disk (always true — RenderWhisperCaptions writes
    captioned alongside the base, never replaces).

    Returns the corrected path. Returns the original path if:
      - whisper IS enabled (operator explicitly wants captioned outputs)
      - path doesn't end in ``_captioned.mp4`` (no-op for the common case)
      - the non-captioned sibling doesn't exist (don't break a publish
        just because the rename guess is wrong — fall through to old path,
        which is no worse than current behavior)
    """
    if not rendered_path or not rendered_path.endswith("_captioned.mp4"):
        return rendered_path

    # Walk niche_config to find whisper_sync.enabled flag. Mirror the
    # lookup chain RenderWhisperCaptions.``_get_whisper_config`` uses so
    # this guard stays in sync with the producer.
    animation = (niche_config or {}).get("animation", {}) or {}
    if not animation:
        animation = (niche_config or {}).get("visuals", {}).get("animation", {}) or {}
    wbw = animation.get("word_by_word", {}) or {}
    ws_enabled = wbw.get("whisper_sync", {}).get("enabled", False)
    if ws_enabled:
        return rendered_path  # operator wants captions; honor it

    # whisper disabled — try the non-captioned sibling
    candidate = rendered_path[: -len("_captioned.mp4")] + ".mp4"
    if Path(candidate).is_file():
        logger.warning(
            "[PUSH] Captioned-MP4 guard: swapped %s → %s (whisper_sync disabled)",
            Path(rendered_path).name,
            Path(candidate).name,
        )
        return candidate
    return rendered_path


def _derive_render_failure_reason(story: dict, clip_index: dict | None, story_id: str) -> str:
    """Why is this blueprint stuck at DRAFTED instead of VISUAL_READY?

    Returns a short prefixed reason string (≤500 chars) suitable for
    ``blueprint.error_message``. Empty string when the blueprint is
    publishable (caller decides by ``_is_publishable``; this helper is
    only called on the not-publishable branch).

    Prefix vocabulary (forward-looking — the dashboard groups by it):
        ``render:validation_failed:<reason>``  — VMAF / spec / no-audio gate
        ``render:compositor_failed:<error>``   — ``_compose_frame`` raised
        ``render:download_failed:<error>``     — yt-dlp couldn't download
        ``render:no_video_found``              — VideoSourcer returned no URL
        ``render:not_publishable``             — last-resort fallback

    The "render:" namespace prefix is a forward-looking choice: if
    other pipeline stages later need to write their own failure
    reasons (e.g. ``publish:rate_limited``, ``write:llm_refused``),
    the prefix keeps them separable for the dashboard explainer.

    Pre-fix: 100% of stuck DRAFTED rows had NULL ``error_message`` —
    weeks of production silent-failure with no diagnostic trail.
    This helper closes that gap; root-cause fixes for the underlying
    yt-dlp / WARP / compositor issues live in separate PRs and read
    from these reasons.
    """
    media = story.get("media") or {}

    # 1. Validation failure — most precise; explicit ``valid: False``
    validation = media.get("video_validation") or {}
    if validation.get("valid") is False:
        # 2026-07-07 diagnostic fix — validate_videos.py writes an
        # ``issues`` list (e.g. ["too_short:13.1s", "colorspace_mismatch"])
        # and/or an ``error`` string ("probe_failed", "exception"), but
        # previously this helper only looked at ``reason`` (never set),
        # so every failure got logged as ``render:validation_failed:
        # unknown``. Live-fire on 2026-07-07 found 100% of gaming
        # DRAFTED blueprints stuck with unknown reasons for days.
        # Prefer ``issues`` (list), fall back to ``error`` (string),
        # final fallback to ``reason`` (legacy) or literal "unknown".
        issues = validation.get("issues") or []
        error_key = validation.get("error", "")
        legacy = validation.get("reason", "")
        if issues:
            reason = ",".join(str(x) for x in issues)
        elif error_key:
            reason = error_key
        elif legacy:
            reason = legacy
        else:
            reason = "unknown"
        return f"render:validation_failed:{reason}"[:500]

    # 2. Compositor failure — ``_compose_frame`` caught an exception and
    # stored ``render_error`` on the story (see base_visual_render.py).
    render_error = media.get("render_error", "")
    if render_error:
        return f"render:compositor_failed:{render_error}"[:500]

    # 3. Download failure — clip_index carries the per-story yt-dlp error
    # string. The strategy sets ``render_status="no_video"`` when the
    # download failed; cross-reference clip_index for the actual error.
    render_status = media.get("render_status", "")
    if render_status == "no_video" or render_status == "":
        clip_entry = (clip_index or {}).get("clips", {}).get(story_id, {})
        clip_error = clip_entry.get("error", "")
        if clip_error:
            return f"render:download_failed:{clip_error}"[:500]
        # 4. No download AND no clip_index error → VideoSourcer returned
        # no candidate at all (the upstream "couldn't find any video"
        # branch in download_top_videos._process_one_story).
        return "render:no_video_found"

    # 5. Render-failed status with no captured exception — defensive
    # fallback (the base ``_compose_frame`` should always populate
    # ``render_error`` on failure, but a subclass override that
    # doesn't would land here).
    if render_status == "render_failed":
        return "render:compositor_failed:no_exception_captured"

    # 6. Last-resort: rendered_path missing for a reason none of the
    # above captured.
    return "render:not_publishable"


def _is_foreign_source(niche_id: str, source: str) -> str | None:
    """Return the matching forbidden prefix if `source` is foreign for `niche_id`, else None."""
    if not source:
        return None
    forbidden = _FORBIDDEN_SOURCE_PREFIXES.get(niche_id, frozenset())
    low = source.lower().strip()
    for prefix in forbidden:
        if low == prefix.rstrip("_") or low.startswith(prefix):
            return prefix
    return None


def _normalize_topic(raw_source: str) -> str:
    """Normalize raw source identifiers to clean topic categories."""
    if not raw_source:
        return "unknown"
    low = raw_source.lower().strip()
    # Check direct mapping
    if low in _TOPIC_MAP:
        return _TOPIC_MAP[low]
    # Check prefix matching
    for prefix, topic in _TOPIC_MAP.items():
        if low.startswith(prefix):
            return topic
    # If it's a long string (raw video title), truncate to a label
    if len(raw_source) > 30:
        return "youtube_content"
    return low


def _get_engagement_arm_boost(client, niche_id: str) -> dict[str, float]:
    """Legacy: boost multipliers derived from historical priority_score.

    Kept as a fallback when bandit posteriors are unavailable. Arms with
    above-average historical priority_score get up to 1.3x, below-average
    down to 0.8x.
    """
    try:
        records = client.blueprints.all(
            formula=f"AND({{niche_id}}='{niche_id}',{{status}}='PUBLISHED')",
            max_records=50,
        )
        arm_scores: dict[str, list[float]] = {}
        for rec in records:
            fields = rec.get("fields", rec)
            arm = fields.get("arm_id") or "default"
            score = float(fields.get("priority_score", 0) or 0)
            if score > 0:
                arm_scores.setdefault(arm, []).append(score)

        if not arm_scores:
            return {}

        arm_avgs = {arm: sum(scores) / len(scores) for arm, scores in arm_scores.items()}
        overall_avg = sum(arm_avgs.values()) / len(arm_avgs) if arm_avgs else 0.5

        boosts: dict[str, float] = {}
        for arm, avg in arm_avgs.items():
            if overall_avg > 0:
                ratio = avg / overall_avg
                boosts[arm] = max(0.8, min(1.3, ratio))
            else:
                boosts[arm] = 1.0
        return boosts
    except Exception as exc:
        # 2026-07-14 audit: was silent `return {}`. When this legacy
        # historical-priority-score fallback fails, callers assume no
        # arms have boost data → treat every arm as boost=1.0 → mask
        # the failure. Elevate to WARNING so operators see when the
        # bandit-posterior-unavailable path is silently degrading.
        logger.warning(
            "[PUSH] _get_engagement_arm_boost failed for niche=%s (%s) — "
            "returning empty boosts; blueprints will rank without "
            "historical priority signal",
            niche_id,
            exc,
        )
        return {}


# Multiplier range applied to priority_score. A sample of 0.0 produces
# the floor (under-performing arms deprioritized); a sample of 1.0
# produces the ceiling (over-performing arms boosted). Mirrors the
# legacy engagement-boost range for behavioural continuity.
_BANDIT_BOOST_FLOOR = 0.7
_BANDIT_BOOST_CEIL = 1.3


def _get_bandit_arm_boost(
    client, niche_id: str, *, arms: dict[str, tuple[float, float]] | None = None
) -> dict[str, float]:
    """Thompson-sampled boost multipliers from bandit_arms posteriors.

    Draws one Beta(alpha, beta) sample per arm and maps it into the
    multiplier range [FLOOR, CEIL] *relative to the per-niche median
    posterior mean*. Linear mapping to [0,1] (the previous behaviour)
    was wrong here because real reward distributions live in 0.02–0.15
    — every arm got deboosted below 1.0 and cold arms with the α=β=1
    prior (mean 0.5) were systematically favoured. Median-relative
    mapping keeps the boost centred at 1.0 so above-median arms get
    boosted and below-median arms get demoted, regardless of the
    absolute reward scale.

    Confidence is implicit in Thompson sampling: high (α+β) tightens
    the sample around the mean, so well-trained arms produce stable
    boosts and fresh arms keep exploring.

    Falls back to {} on any error so the caller can degrade to the
    engagement-history boost or neutral multipliers.

    PR #399: ``arms`` kwarg lets the caller pre-load the per-niche
    bandit arms ONCE and pass to both this function and
    :func:`_get_bandit_arm_n_obs`. Pre-PR each call re-fetched the
    bandit_arms table via ``load_all_arms`` — the PushToBacklog stage
    called both, so the table was scanned twice per pipeline run per
    niche. With the kwarg, one fetch + two uses.
    """
    if arms is None:
        try:
            from genlab_core.learning.arm_loader import load_all_arms
        except ImportError:
            return {}
        proxy = getattr(client, "bandit_arms", None)
        if proxy is None:
            return {}
        arms = load_all_arms(proxy, niche_id)

    if not arms:
        return {}

    import random

    means = [a / (a + b) for (a, b) in arms.values() if (a + b) > 0]
    # Use median as the neutral reference. With α=β=1 priors mixed
    # with trained arms, the simple mean is pulled by the priors;
    # median is more robust to that skew. For even-count arrays we
    # interpolate between the two middle values so the reference
    # truly sits at the midpoint (otherwise with just two arms the
    # median collapses to the upper value and the high arm always
    # clamps to a boost of 1.0).
    if means:
        sorted_means = sorted(means)
        n = len(sorted_means)
        if n % 2 == 1:
            median_mean = sorted_means[n // 2]
        else:
            median_mean = (sorted_means[n // 2 - 1] + sorted_means[n // 2]) / 2
    else:
        median_mean = 0.5
    # Avoid divide-by-zero in the rare case where every arm has mean 0.
    if median_mean <= 0:
        median_mean = 0.5

    boosts: dict[str, float] = {}
    for arm_id, (alpha, beta) in arms.items():
        a = alpha if alpha > 0 else 1.0
        b = beta if beta > 0 else 1.0
        try:
            sample = random.betavariate(a, b)
        except (ValueError, OverflowError):
            sample = median_mean

        # Map sample so sample == median_mean -> boost = 1.0.
        # Below median linearly approaches FLOOR at sample = 0.
        # Above median linearly approaches CEIL at sample = 2 * median.
        if sample <= median_mean:
            ratio = sample / median_mean
            boost = _BANDIT_BOOST_FLOOR + (1.0 - _BANDIT_BOOST_FLOOR) * ratio
        else:
            # 2 * median_mean as the "double the niche-average" headroom.
            # Clamp to CEIL beyond that so the boost never blows out.
            ratio = min(1.0, (sample - median_mean) / median_mean)
            boost = 1.0 + (_BANDIT_BOOST_CEIL - 1.0) * ratio
        boosts[arm_id] = round(boost, 4)
    return boosts


# Backwards-compatible alias for any external import.
_get_arm_boost = _get_bandit_arm_boost


def _get_bandit_arm_n_obs(
    client, niche_id: str, *, arms: dict[str, tuple[float, float]] | None = None
) -> dict[str, int]:
    """Per-arm observation count derived from the Thompson posteriors.

    With α=β=1 priors, n_obs = (α-1) + (β-1) = α+β-2. Used by the
    ε-greedy force-explore in _classify_arm to detect under-explored
    style:* arms.

    Returns {} on any error so callers can degrade gracefully (the
    force-explore short-circuits when arm_n_obs is empty/None).

    PR #399: ``arms`` kwarg lets the caller pre-load the per-niche
    bandit arms ONCE and pass to both this function and
    :func:`_get_bandit_arm_boost`. See that function's docstring for
    the rationale.
    """
    if arms is None:
        try:
            from genlab_core.learning.arm_loader import load_all_arms
        except ImportError:
            return {}
        proxy = getattr(client, "bandit_arms", None)
        if proxy is None:
            return {}
        arms = load_all_arms(proxy, niche_id)
    return {arm_id: max(0, int(alpha + beta - 2)) for arm_id, (alpha, beta) in arms.items()}


def _load_linucb_arms(
    client,
    niche_id: str,
    *,
    extended: dict[str, dict[str, Any]] | None = None,
) -> dict:
    """Load persisted LinUCBArm state for each arm in the niche.

    Returns a dict mapping arm_id -> LinUCBArm. Returns {} on any error
    so callers degrade to Thompson-only ranking. Cached at module level
    per (niche_id, generation count) so we don't deserialize matrices
    on every story.

    PR #400: ``extended`` kwarg lets the caller pre-fetch the
    bandit_arms records via ``load_all_arms_extended`` and pass the
    parsed dict here. Without the kwarg, this function does its OWN
    ``proxy.all()`` — pre-PR PushToBacklog.execute() therefore made
    3 separate scans of bandit_arms (one each for boost, n_obs, and
    LinUCB matrices). With the kwarg, all 3 share a single fetch.

    The ``extended`` dict shape is::

        {arm_id: {"alpha": float, "beta": float, "linucb_state": dict|None}}

    which is exactly what ``load_all_arms_extended`` returns.
    """
    try:
        import json as _json

        from genlab_core.learning.linucb import CONTEXT_DIM, LinUCBArm
    except ImportError:
        return {}

    # Fast path: caller pre-loaded the extended records — build LinUCBArms
    # from the already-parsed linucb_state dicts (no JSON re-parse, no
    # second proxy.all()).
    if extended is not None:
        arms: dict = {}
        for arm_id, entry in extended.items():
            state = entry.get("linucb_state")
            if state is None:
                arms[arm_id] = LinUCBArm(d=CONTEXT_DIM)
                continue
            try:
                arms[arm_id] = LinUCBArm.from_dict(state)
            except Exception:
                arms[arm_id] = LinUCBArm(d=CONTEXT_DIM)
        return arms

    # Legacy path: standalone fetch when no preload is provided.
    proxy = getattr(client, "bandit_arms", None)
    if proxy is None:
        return {}

    arms = {}
    try:
        for item in proxy.all():
            fields = item.get("fields", item)
            if fields.get("niche_id") != niche_id:
                continue
            arm_id = fields.get("arm_id", "")
            if not arm_id:
                continue
            raw = fields.get("linucb_state") or ""
            if not raw:
                arms[arm_id] = LinUCBArm(d=CONTEXT_DIM)
                continue
            try:
                state = _json.loads(raw) if isinstance(raw, str) else raw
                arms[arm_id] = LinUCBArm.from_dict(state)
            except Exception:
                arms[arm_id] = LinUCBArm(d=CONTEXT_DIM)
    except Exception as exc:
        # Task #633 (2026-07-09 late evening): elevated DEBUG →
        # WARNING. When LinUCB arm load fails silently, ALL push_to_
        # backlog decisions fall back to the pre-LinUCB path — every
        # blueprint picks arms without LinUCB context, IPS propensity
        # goes to None, and the LinUCB posteriors accumulate nothing.
        # Silent-fail here means the entire contextual bandit is
        # effectively disabled without operator visibility. Same
        # class of bug as tonight's motion_compositor (#631).
        logger.warning(
            "[PUSH] LinUCB arm load failed (%s) — this pipeline run will "
            "fall back to non-LinUCB arm selection; contextual bandit "
            "will accumulate no observations for this batch.",
            exc,
        )
        return {}
    return arms


def _apply_engagement_boost(
    base_score: float,
    arm_id: str,
    boosts: dict[str, float],
    linucb_arms: dict | None = None,
    story: dict | None = None,
    niche_id: str | None = None,
) -> float:
    """Apply engagement-based boost to priority score.

    When ``linucb_arms`` + ``story`` + ``niche_id`` are provided, the
    Thompson-sampled boost is further shifted by the per-arm LinUCB UCB
    score against this story's context vector. Above-prior LinUCB scores
    push the boost up; below-prior pushes it down. With LinUCB neutral
    (n_obs<5 or singular matrix) the Thompson boost is the sole signal.

    Why this wiring exists: LinUCBArm.update() is called on every 48h
    reward and persisted as the linucb_state JSONB column, but until
    2026-05-21 no production code path called LinUCBArm.predict() at
    selection time. The matrices were data-collected-but-never-read.
    """
    multiplier = boosts.get(arm_id, 1.0)
    if linucb_arms and story is not None and niche_id and arm_id in linucb_arms:
        try:
            arm = linucb_arms[arm_id]
            if arm.n_obs >= 5:
                from genlab_core.learning.linucb import build_content_context

                ctx = build_content_context(story, niche_id)
                ucb = arm.predict(ctx)
                # UCB lives roughly in [0, 1.5] for our reward scale.
                # Treat 0.5 as neutral so well-calibrated arms with low
                # exploration bonus settle near 1.0x. Cap delta at ±20%.
                delta = max(-0.20, min(0.20, (ucb - 0.5) * 0.4))
                multiplier = multiplier * (1.0 + delta)
        except Exception as exc:
            logger.warning(
                "[PUSH] LinUCB scoring failed for niche=%s — priority "
                "multiplier stays at %.3f (no UCB signal): %s",
                niche_id,
                multiplier,
                exc,
                exc_info=True,
            )
    return round(base_score * multiplier, 4)


# Force-explore parameters for under-explored style:* arms.
# 2026-06-14 prod audit found movies and sports had 0/5 converged style
# arms — the bandit's exploitation policy never picked an unobserved arm
# when the dominant arm had 100+ obs of mediocre reward. ε-greedy here
# breaks that lock-in: with probability EPSILON, when the niche has
# fewer than CONVERGED_STYLE_THRESHOLD converged style arms, pick a
# random under-explored style arm instead of the keyword-matched arm.
#
# CONVERGED = arm has n_obs >= 3 (escapes the α=β=1 prior into a
# meaningful posterior, while keeping convergence time reachable at
# typical pipeline cadence).
#
# Parameters validated by simulation against 2026-06-14 prod bandit
# state (/tmp/simulate_v3.py). At ε=0.25 + 1 publish/day:
#
#   sports:      46 days to 3-converged
#   movies:      46 days
#   anime:       36 days
#   ai_creators: 22 days
#   gaming:      already converged (no-op)
#
# An earlier draft used n_obs == 0 (unobserved only) which only seeded
# the FIRST observation per arm and could never drive any arm to
# convergence — the simulation showed >120 days at every parameter
# choice. The fix: pick from under-explored (n_obs < MIN_OBS_CONVERGED),
# so a chosen arm grows from 1 → 2 → 3 across repeated force-explores.
_FORCE_EXPLORE_EPSILON = 0.25  # 1 in 4 publishes picks an under-explored style arm
_CONVERGED_STYLE_THRESHOLD = 3  # niches with fewer converged style arms get force-explored
_MIN_OBS_FOR_CONVERGED = 3  # n_obs needed for an arm to count as "converged"


def _classify_arm(
    niche_id: str,
    story: dict,
    content: dict,
    arm_boosts: dict[str, float] | None = None,
    arm_n_obs: dict[str, int] | None = None,
    _rng=None,
    linucb_arms: dict | None = None,
    context=None,
    _random_control_rng=None,
    active_experiment=None,
    experiment_assignment_id: str = "",
) -> str:
    """Backward-compat wrapper around :func:`_classify_arm_with_propensity`.

    Returns just the arm_id, dropping the IPS propensity. Production
    code paths now call :func:`_classify_arm_with_propensity` directly
    so they can persist propensity into the blueprint fields dict; this
    wrapper preserves the ``str``-only signature for the 20+ test
    callers in ``tests/test_classify_arm_*.py`` that pre-date the IPS
    wire-up.

    See :func:`_classify_arm_with_propensity` for the full behavior
    contract.
    """
    arm_id, _propensity = _classify_arm_with_propensity(
        niche_id,
        story,
        content,
        arm_boosts=arm_boosts,
        arm_n_obs=arm_n_obs,
        _rng=_rng,
        linucb_arms=linucb_arms,
        context=context,
        _random_control_rng=_random_control_rng,
        active_experiment=active_experiment,
        experiment_assignment_id=experiment_assignment_id,
    )
    return arm_id


def _classify_arm_with_propensity(
    niche_id: str,
    story: dict,
    content: dict,
    arm_boosts: dict[str, float] | None = None,
    arm_n_obs: dict[str, int] | None = None,
    _rng=None,
    linucb_arms: dict | None = None,
    context=None,
    _random_control_rng=None,
    active_experiment=None,
    experiment_assignment_id: str = "",
    arm_posteriors: dict[str, tuple[float, float]] | None = None,
) -> tuple[str | None, float | None]:
    """Classify content into a bandit arm_id, with optional IPS propensity.

    Keyword matching picks the candidate set: any arm whose keyword
    list intersects the story+hook+summary text is a contender. When
    only one arm matches, return it directly (the common case).

    When MULTIPLE arms match — e.g. a story that's both a trailer and
    a cast announcement — fall back to the bandit posterior to pick
    between them. The arm with the highest current boost wins. This
    is a soft form of bandit-driven classification: the keyword set
    constrains the choice to semantically appropriate arms, and the
    bandit breaks the tie based on which arm has historically driven
    more engagement.

    With ``arm_boosts=None``, behaviour matches the legacy first-
    matching-arm-wins order so callers that don't pass boosts (tests,
    pre-bandit code paths) keep their existing semantics.

    2026-06-14 ε-greedy force-explore (optional)
    --------------------------------------------
    When ``arm_n_obs`` is provided AND the niche has fewer than
    ``_CONVERGED_STYLE_THRESHOLD`` converged ``style:*`` arms, with
    probability ``_FORCE_EXPLORE_EPSILON`` pick a random UNOBSERVED
    ``style:*`` arm instead of the keyword-classified arm. Seeds the
    bandit with exploration signal in niches that would otherwise
    lock in on their default arm forever.

    ``_rng`` is the random-number source (defaults to ``random.random``);
    tests inject a deterministic value to pin the ε-greedy decision.

    2026-06-21 LinUCB-driven tie-break (Lever I2 wiring, opt-in)
    ------------------------------------------------------------
    When ``linucb_arms`` AND ``context`` are both provided AND
    ``GENLAB_LINUCB_PICK_ENABLED=1``, the multi-match tie-break tries
    ``linucb_picker.pick_best_arm`` FIRST — picks the arm with the
    highest UCB score given the story's context vector. When the picker
    returns None (any matched arm cold-started), falls back to today's
    Thompson-boost logic. Default (env unset) → legacy Thompson path.

    2026-06-21 Random-control wrap (Lever I1 wiring, opt-in)
    --------------------------------------------------------
    When ``GENLAB_EXPERIMENTATION_ENABLED=1`` AND the multi-match tie-
    break ran (i.e. we computed a ``bandit_pick``), wrap the final pick
    with ``experimentation.select_arm_with_random_control``. With
    probability ``epsilon`` (default 5%) the function returns a random
    pick from ``matches`` instead of the bandit pick — generates clean
    counterfactual data without hurting baseline performance. Single-
    match and no-match paths bypass random control (nothing to
    randomize over). ``_random_control_rng`` is a ``random.Random``
    instance injectable by tests for deterministic verification;
    production callers pass nothing → fresh Random per invocation.

    2026-06-21 Active experiment override (Lever I3 wiring, opt-in)
    --------------------------------------------------------------
    When ``active_experiment`` AND ``experiment_assignment_id`` are
    provided AND ``GENLAB_EXPERIMENTATION_ENABLED=1``, the registered
    experiment's deterministic assignment OVERRIDES everything below.
    The assigned arm is returned immediately — before force-explore,
    keyword matching, LinUCB tie-break, or random control. This is the
    "operator-deliberate" override: a registered experiment with
    pre-declared allocation always wins because operators want
    statistical signal on a specific hypothesis. Falls through to the
    rest of the chain when ``assign_to_experiment`` returns None
    (inactive experiment, invalid allocation, etc.).

    2026-06-28 IPS propensity wire-through (PR U)
    ---------------------------------------------
    Returns ``(arm_id, propensity)`` so the caller can persist the
    propensity into the ``pending_feedback.propensity`` column shipped
    by PR #634. ``propensity`` is non-None only when the LinUCB picker
    path actually ran AND returned an arm (i.e. opt-in env on +
    linucb_arms + context + no cold-start). Single-candidate short-
    circuit returns ``(arm, 1.0)`` because the pick is trivially
    deterministic. All other paths — active experiment, force-explore,
    no-match default, Thompson-boost fallback, random-control override
    — return ``(arm, None)`` because they have no IPS-compatible
    selection-probability concept (random-control has its OWN selection
    probability that lives in ``ExperimentationSelection``, not here).

    The backward-compat wrapper :func:`_classify_arm` drops the
    propensity so the 20+ test callers that pre-date the wire-up don't
    need updating.
    """
    text = f"{story.get('title', '')} {content.get('hook', '')} {story.get('summary', '')}".lower()

    # Active experiment override (Lever I3 wiring) — runs FIRST so a
    # registered hypothesis always gets its sample regardless of what
    # the bandit / keyword classifier would have picked. Opt-in: env
    # flag must be set AND caller must pass both active_experiment +
    # a stable assignment_id for deterministic-by-blueprint bucket
    # placement.
    if active_experiment is not None and experiment_assignment_id:
        try:
            from genlab_core.learning import experimentation

            if experimentation.is_enabled():
                sel = experimentation.assign_to_experiment(
                    experiment_assignment_id, active_experiment
                )
                if sel is not None:
                    logger.info(
                        "[classify_arm] active experiment %s assigned id=%s -> %s",
                        active_experiment.experiment_id,
                        experiment_assignment_id,
                        sel.arm_id,
                    )
                    # 2026-06-23 — close the 8-event taxonomy. The LAST
                    # dormant event type: EVENT_EXPERIMENT_ASSIGNMENT.
                    # Fires only when assign_to_experiment() actually
                    # returns a selection (experiment is registered,
                    # enabled env flag set, deterministic-bucket math
                    # succeeded). Sibling to EVENT_BANDIT_PICK (line
                    # ~1576) which fires AFTER classification regardless
                    # of path; this one fires at the precise moment the
                    # experiment branch wins. With both emits a later
                    # query can answer "of the N times this niche
                    # received candidates, how many were experiment-
                    # assigned vs bandit-picked?" without inferring it
                    # from arm_id naming conventions.
                    try:
                        from genlab_core.learning.episodic_memory import (
                            EVENT_EXPERIMENT_ASSIGNMENT,
                            record_event,
                        )

                        record_event(
                            event_type=EVENT_EXPERIMENT_ASSIGNMENT,
                            niche_id=niche_id,
                            blueprint_id=experiment_assignment_id,
                            payload={
                                "experiment_id": active_experiment.experiment_id,
                                "arm_id": sel.arm_id,
                                "assignment_id": experiment_assignment_id,
                                "arms_in_experiment": len(active_experiment.arms),
                            },
                        )
                    except Exception as exc:  # noqa: BLE001 — episodic emit must never block classification
                        logger.debug(
                            "[classify_arm] episodic experiment_assignment emit failed: %s",
                            exc,
                        )
                    # Active-experiment path: propensity is None — the
                    # experiment's deterministic-bucket assignment has
                    # its own (declared in YAML) allocation probability
                    # that doesn't map onto the LinUCB softmax IPS
                    # convention. Keeping propensity=None here makes
                    # downstream IPS replay exclude experiment rows
                    # cleanly (NULL is the "not applicable" sentinel).
                    return sel.arm_id, None
        except Exception as exc:  # noqa: BLE001 — fail-open to bandit chain
            logger.warning(
                "[classify_arm] experiment assignment error — falling through "
                "to bandit chain (experiment allocation lost for niche=%s): %s",
                niche_id,
                exc,
                exc_info=True,
            )

    # ε-greedy force-explore — runs BEFORE keyword matching so it
    # actually escapes the keyword set (the bug we're fixing: keyword
    # matching always routes back to the niche default, blocking
    # exploration of alternate hook styles).
    if arm_n_obs is not None:
        forced = _maybe_force_explore_style_arm(niche_id, arm_n_obs, _rng=_rng)
        if forced is not None:
            # Force-explore is a uniform pick over unobserved style:*
            # arms. The selection probability IS knowable (1/count) but
            # the rest of the IPS infrastructure only treats LinUCB-
            # path propensities; surfacing a non-LinUCB propensity here
            # would mix conventions. Keep None until force-explore gets
            # its own IPS treatment.
            return forced, None

    matches: list[str] = []
    for arm_id, keywords in _ARM_KEYWORDS.get(niche_id, []):
        if any(kw in text for kw in keywords):
            matches.append(arm_id)

    if not matches:
        return _NICHE_ARM_DEFAULTS.get(niche_id, "default"), None

    # 2026-07-14: bandit was descriptive, not prescriptive.
    # ----------------------------------------------------
    # Pre-fix: `len(matches) == 1` returned (arm, 1.0) — a deterministic
    # short-circuit that ran BEFORE LinUCB / Thompson-boost. 92% of prod
    # picks hit this branch. Bandit posteriors correctly identified that
    # some arms (e.g. ai_creators.comparison_test with n=31, avg_r=0.143)
    # substantially outperformed the default arm (tool_demo n=32, avg_r=
    # 0.082), yet those higher-reward arms were picked ~0 times because
    # keyword-matched single-arm bypassed the picker entirely.
    #
    # Fix: when there's exactly ONE keyword match AND we have Thompson
    # boost data indicating an alternative arm has a materially higher
    # posterior mean, promote the case to multi-match by adding the
    # top-2 higher-boost alternatives. The existing LinUCB→Thompson→
    # first-match chain then arbitrates. The keyword-matched arm is
    # always a candidate — if it's actually best under LinUCB context
    # + arm_boosts, it still wins. If a genuinely better arm exists,
    # the bandit gets to explore/exploit it.
    #
    # Bounded exploration: only add top-2 alternatives (not all arms)
    # so we preserve keyword semantics as the dominant signal. Threshold
    # of 1.10× multiplicative gap on the boost avoids adding
    # marginal-difference alternatives (α=β=1 cold arms hover at ~1.0).
    if len(matches) == 1 and arm_boosts:
        matched_boost = arm_boosts.get(matches[0], 1.0)
        alternatives = sorted(
            [
                (arm_id, boost)
                for arm_id, boost in arm_boosts.items()
                if arm_id != matches[0] and boost > matched_boost * 1.10
            ],
            key=lambda kv: kv[1],
            reverse=True,
        )[:2]
        if alternatives:
            matches = matches + [arm_id for arm_id, _ in alternatives]
            logger.info(
                "[classify_arm] single-match arm %s (boost=%.2f) — "
                "promoted to multi-match with alternatives %s (boosts %s) "
                "so LinUCB/Thompson can arbitrate",
                matches[0],
                matched_boost,
                [arm_id for arm_id, _ in alternatives],
                [f"{b:.2f}" for _, b in alternatives],
            )
            # Fall through to multi-match branch below — LinUCB/Thompson
            # sees the expanded candidate set.

    if len(matches) == 1:
        # Single-candidate short-circuit. Reached only when arm_boosts
        # is None/empty (cold-start pre-any-reward) OR no alternative
        # crossed the 1.10× promotion threshold. Propensity = 1.0
        # because the selection is trivially deterministic.
        return matches[0], 1.0

    # Compute the bandit pick. The three-path structure (LinUCB →
    # Thompson → first-match) converges to a single ``bandit_pick``
    # variable so the I1 random-control wrap below has one input.
    # ``bandit_propensity`` rides alongside — only the LinUCB path
    # produces a non-None value.
    bandit_pick: str | None = None
    bandit_propensity: float | None = None

    # LinUCB-driven tie-break first (Lever I2 wiring). Opt-in: env
    # flag must be set AND caller must pass both linucb_arms + context.
    # Returns None on cold-start (any matched arm under-observed) → we
    # fall through to the Thompson-boost path below.
    if linucb_arms and context is not None:
        try:
            from genlab_core.learning import linucb_picker

            if linucb_picker.is_enabled():
                # Intelligence #8 (2026-07-18): per-niche stochastic
                # sampling gate. When
                # ``GENLAB_LINUCB_STOCHASTIC_ENABLED_{NICHE}=1``, arms
                # are sampled from softmax(scores/T) instead of argmax.
                # Makes IPS/DR estimators mathematically meaningful for
                # this niche's decisions at the cost of some exploration
                # variance. Global flag
                # ``GENLAB_LINUCB_STOCHASTIC_ENABLED=1`` acts as fallback
                # to enable across all niches. Defaults off — preserves
                # pre-#8 argmax behavior on niches without the flag set.
                import os as _os

                _niche_flag = _os.environ.get(
                    f"GENLAB_LINUCB_STOCHASTIC_ENABLED_{niche_id.upper()}"
                )
                if _niche_flag is not None:
                    _stochastic = _niche_flag.strip() == "1"
                else:
                    _stochastic = (
                        _os.environ.get("GENLAB_LINUCB_STOCHASTIC_ENABLED", "0").strip() == "1"
                    )

                # PR U (2026-06-28): use the propensity-aware variant
                # so the IPS column on pending_feedback gets populated.
                # The arm-selection rule is identical to pick_best_arm
                # — only the (arm, propensity) tuple-return differs.
                bandit_pick, bandit_propensity = linucb_picker.pick_best_arm_with_propensity(
                    matches, context, linucb_arms, stochastic=_stochastic
                )
        except Exception as exc:  # noqa: BLE001 — fail-open to Thompson
            logger.warning(
                "[classify_arm] LinUCB picker error — falling back to "
                "Thompson (bandit choice degraded, propensity=NULL): %s",
                exc,
                exc_info=True,
            )
            bandit_pick = None
            bandit_propensity = None

    # Thompson-boost tie-break (today's logic) when LinUCB didn't run
    # or returned None (cold-start). Falls back to the first match if
    # no boost data is available for the matched arms.
    #
    # Intelligence #8b (2026-07-18): the pre-existing comment claimed
    # "Thompson has no IPS-compatible propensity concept". That's true
    # for the point-in-time propensity but conflates it with the
    # MARGINAL propensity (P(arm wins Thompson pick) integrated over
    # Beta draws) which IS what IPS wants. When the per-niche flag
    # ``GENLAB_THOMPSON_PROPENSITY_ENABLED_{NICHE}=1`` is set, we
    # estimate this via Monte Carlo (~200 Beta draws per arm) and log
    # it alongside the pick. Enables meaningful IPS/DR on the ~96% of
    # decisions that currently bypass LinUCB entirely.
    if bandit_pick is None:
        if not arm_boosts:
            bandit_pick = matches[0]
        else:
            bandit_pick = max(matches, key=lambda a: arm_boosts.get(a, 1.0))

            # Intelligence #8b: Thompson propensity capture. Only fire
            # when the per-niche flag is set + we have raw posteriors
            # to draw from. arm_posteriors is None when the caller
            # didn't thread it through — degrades to Thompson-picks-
            # with-null-propensity behavior (pre-#8b).
            if arm_posteriors:
                try:
                    from genlab_core.learning.thompson_propensity import (
                        compute_thompson_propensity,
                        is_thompson_propensity_enabled,
                    )

                    if is_thompson_propensity_enabled(niche_id):
                        # Restrict to matched arms only — Thompson picks
                        # argmax over MATCHES, not over all arms.
                        matched_posteriors = {
                            a: arm_posteriors[a] for a in matches if a in arm_posteriors
                        }
                        if matched_posteriors:
                            bandit_propensity = compute_thompson_propensity(
                                bandit_pick, matched_posteriors
                            )
                            logger.debug(
                                "[classify_arm] Thompson propensity for %s: %.4f "
                                "(niche=%s, n_matches=%d)",
                                bandit_pick,
                                bandit_propensity,
                                niche_id,
                                len(matched_posteriors),
                            )
                except Exception as exc:  # noqa: BLE001 — fail-open to None
                    logger.warning(
                        "[classify_arm] Thompson propensity capture failed for "
                        "niche=%s arm=%s — propensity stays NULL (IPS excludes "
                        "this row): %s",
                        niche_id,
                        bandit_pick,
                        exc,
                        exc_info=True,
                    )

    # Random-control wrap (Lever I1 wiring). With probability epsilon
    # (default 5%) override the bandit pick with a uniform-random pick
    # from ``matches`` — generates counterfactual data so the bandit
    # eventually learns whether arm B would have worked when arm A
    # was always chosen. Opt-in via GENLAB_EXPERIMENTATION_ENABLED=1.
    # When the random-control fires, propensity collapses to None — the
    # override's selection probability is independent of the LinUCB
    # softmax weight and a downstream IPS estimator would treat the
    # two propensity flavors uniformly if we mixed them here.
    try:
        from genlab_core.learning import experimentation

        if experimentation.is_enabled():
            sel = experimentation.select_arm_with_random_control(
                bandit_pick=bandit_pick,
                all_arms=matches,
                rng=_random_control_rng,
            )
            if sel.selection_mode == experimentation.SELECTION_MODE_RANDOM_CONTROL:
                logger.info(
                    "[classify_arm] random control overrode bandit pick %s -> %s",
                    bandit_pick,
                    sel.arm_id,
                )
                # Random-control wins — propensity drops to None
                # because the LinUCB pick was discarded.
                return sel.arm_id, None
            # Random-control didn't override: the bandit pick stands.
            # Preserve the LinUCB propensity (or None if Thompson ran).
            return sel.arm_id, bandit_propensity
    except Exception as exc:  # noqa: BLE001 — fail-open to bandit pick
        logger.warning(
            "[classify_arm] experimentation wrap error — random-control skipped "
            "(no counterfactual data generated for niche=%s): %s",
            niche_id,
            exc,
            exc_info=True,
        )

    return bandit_pick, bandit_propensity


def _maybe_force_explore_style_arm(
    niche_id: str,
    arm_n_obs: dict[str, int],
    _rng=None,
) -> str | None:
    """Return an unobserved ``style:*`` arm with probability ε when the
    niche is exploration-starved; else None.

    Two gates:

    1. **Exploration-need gate** — count the niche's ``style:*`` arms
       with n_obs >= _MIN_OBS_FOR_CONVERGED. If that count is already
       at or above _CONVERGED_STYLE_THRESHOLD, the bandit has enough
       per-style signal to make good decisions — don't force-explore.

    2. **ε-greedy gate** — with probability _FORCE_EXPLORE_EPSILON,
       trigger the exploration. Otherwise let the normal classifier
       take over.

    Both gates must be open. Returns None otherwise.

    When triggered, picks UNIFORMLY at random from the niche's
    UNDER-EXPLORED ``style:*`` arms (n_obs < _MIN_OBS_FOR_CONVERGED).
    An arm stays eligible until it converges, so repeated force-explores
    can drive a single arm from 1 → 2 → 3 obs over time. (An earlier
    draft used n_obs == 0 — simulation showed that only seeded the
    first observation per arm and never drove convergence.) If every
    style arm has already converged, returns None and lets the classifier
    proceed.

    Pure function — no DB calls. Caller passes ``arm_n_obs`` from
    the bandit-loader's posterior data.
    """
    import random

    rand = _rng if _rng is not None else random.random

    # Identify the niche's style:* arms and partition by exploration state.
    style_prefix = f"style:{niche_id}:"
    style_arms = [arm for arm in arm_n_obs if arm.startswith(style_prefix)]
    if not style_arms:
        return None

    converged = [a for a in style_arms if arm_n_obs.get(a, 0) >= _MIN_OBS_FOR_CONVERGED]
    if len(converged) >= _CONVERGED_STYLE_THRESHOLD:
        return None  # niche has enough per-style signal already

    # ε-greedy gate
    if rand() >= _FORCE_EXPLORE_EPSILON:
        return None

    unobserved = [a for a in style_arms if arm_n_obs.get(a, 0) < _MIN_OBS_FOR_CONVERGED]
    if not unobserved:
        return None  # everything explored at least once

    # Pick uniformly. Using ``random.choice`` (not the injected _rng)
    # so the test injects ε-decision determinism while leaving the
    # pick uniform across unobserved arms.
    return random.choice(unobserved)


class PushToBacklog:
    """Push stories and blueprints to the shared SharePoint backlog.

    Niche-agnostic: ``niche_id`` is read from ``context["niche_id"]``
    at execution time. Raises ``ValueError`` if the key is missing.
    """

    def __init__(self) -> None:
        self._client: BacklogClient | None = None

    def _get_client(self, context: dict[str, Any]) -> BacklogClient:
        """Lazy-initialize BacklogClient.

        Config path resolution (in priority order):
          1. context["backlog_config_path"] — explicit override
          2. context["niche_root"] / config / lists_config.yaml — niche dir
          3. Fall through to BacklogClient() defaults (BACKLOG_CONFIG_PATH
             env var → CWD walk)
        """
        if self._client is None:
            config_path = context.get("backlog_config_path")

            if not config_path:
                niche_root = context.get("niche_root")
                if niche_root:
                    candidate = Path(niche_root) / "config" / "lists_config.yaml"
                    if candidate.exists():
                        config_path = str(candidate)
                        logger.debug(
                            "[PUSH] Resolved backlog config from niche_root: %s",
                            config_path,
                        )

            if config_path:
                self._client = BacklogClient(config_path=config_path)
            else:
                self._client = BacklogClient()
        return self._client

    def execute(self, context: StageContext) -> StageContext:  # noqa: C901
        niche_id = context.get("niche_id")
        if not niche_id:
            raise ValueError(
                "PushToBacklog requires context['niche_id'] to be set. "
                "Ensure the pipeline runner populates niche_id before this stage."
            )

        stories = context.get("stories", [])
        if not stories:
            logger.info("[PUSH] No stories to push")
            return context

        _use_postgres = os.getenv("GENLAB_USE_POSTGRES", "").lower() == "true"
        if not _use_postgres and not all(
            [
                settings.azure_tenant_id,
                settings.azure_client_id,
                settings.azure_client_secret,
                settings.sharepoint_site_id,
            ]
        ):
            logger.warning("[PUSH] Azure/SharePoint credentials missing, skipping backlog push")
            context.setdefault("run_stats", {})["backlog_push"] = {
                "stories_pushed": 0,
                "blueprints_pushed": 0,
                "status": "skipped_no_credentials",
            }
            return context

        try:
            client = self._get_client(context)
        except Exception as e:
            logger.error("[PUSH] Failed to initialize BacklogClient: %s", e)
            context.setdefault("run_stats", {})["backlog_push"] = {
                "stories_pushed": 0,
                "blueprints_pushed": 0,
                "status": f"error_init: {e}",
            }
            return context

        stories_pushed = 0
        blueprints_pushed = 0
        video_dedup_skipped = 0
        errors: list[str] = []

        # Load recent hooks for this niche to prevent cross-run duplicates.
        # Time-windowed to prevent hook starvation as history grows.
        #
        # Only blueprints in an *active* state (see _BLOCKING_STATUSES) count
        # as existing hooks. Rows in ARCHIVED (whether rejected by the user
        # or auto-archived by health_monitor.check_missing_media) don't
        # represent content we're committed to — they're free to re-emit.
        # Including them silently poisoned the dedup set for every re-run.
        existing_hooks: set[str] = set()
        non_blocking_skipped = 0
        recent_bps: list = []
        try:
            from datetime import datetime, timedelta

            _hook_cutoff = datetime.now(UTC) - timedelta(days=30)
            recent_bps = client.blueprints.all(
                formula=f"{{niche_id}}='{niche_id}'",
                max_records=2000,
            )
            for bp in recent_bps:
                fields = bp.get("fields", bp)
                if not _is_blocking(bp):
                    non_blocking_skipped += 1
                    continue
                _bp_created = fields.get("created_at")
                if isinstance(_bp_created, str):
                    try:
                        _bp_created = datetime.fromisoformat(_bp_created.replace("Z", "+00:00"))
                    except (ValueError, TypeError):
                        _bp_created = None
                if _bp_created and _bp_created < _hook_cutoff:
                    continue
                h = (fields.get("hook") or "").strip().lower()
                if h:
                    existing_hooks.add(h)
            if existing_hooks or non_blocking_skipped:
                logger.info(
                    "[PUSH] Loaded %d existing hooks for dedup (skipped %d non-blocking)",
                    len(existing_hooks),
                    non_blocking_skipped,
                )
        except Exception as e:
            # 2026-07-21 (rule #19): elevated. hook-dedup load failure
            # means dedup runs against smaller/empty set → duplicate
            # hooks slip through as new blueprints.
            logger.warning("[PUSH] Could not load existing hooks: %s", e, exc_info=True)
        context["existing_hooks"] = existing_hooks

        # PR #400 (extending PR #399): pre-load EXTENDED bandit-arm
        # records ONCE for the niche. ``load_all_arms_extended`` returns
        # ``{arm_id: {alpha, beta, linucb_state}}`` — the union of what
        # all 3 downstream consumers need. Each gets passed the same
        # preloaded dict (via different kwargs).
        #
        # Pre-PR #399: 3 separate ``proxy.all()`` scans (one each for
        # boost, n_obs, LinUCB matrices). PR #399 collapsed boost+n_obs
        # to 1 fetch using ``load_all_arms``. This PR (#400) folds the
        # 3rd LinUCB fetch into the same scan via
        # ``load_all_arms_extended``. **1 scan total** instead of 3.
        try:
            from genlab_core.learning.arm_loader import load_all_arms_extended

            _arm_proxy = getattr(client, "bandit_arms", None)
            preloaded_extended: dict[str, dict[str, Any]] | None = (
                load_all_arms_extended(_arm_proxy, niche_id) if _arm_proxy is not None else None
            )
        except Exception as exc:
            # 2026-07-14 audit: was silent `preloaded_extended = None`.
            # When PR #400's single-scan bandit-arm load fails, downstream
            # falls back to per-arm queries (3× the DB hits). More
            # importantly, if the failure is a schema/permission drift,
            # per-arm queries will also fail and blueprints get scored
            # without ANY bandit signal — but operators see nothing.
            # Elevate to WARNING so class-of-bug surfaces.
            logger.warning(
                "[PUSH] load_all_arms_extended failed for niche=%s (%s) — "
                "falling back to per-arm queries (3× DB cost); if downstream "
                "loaders also fail, blueprints will rank without bandit signal",
                niche_id,
                exc,
            )
            preloaded_extended = None

        # Derive the legacy ``{arm_id: (alpha, beta)}`` tuple shape that
        # ``_get_bandit_arm_boost`` and ``_get_bandit_arm_n_obs`` accept
        # via their ``arms`` kwarg (PR #399). Trivial transform — no
        # extra DB hit.
        preloaded_arms: dict[str, tuple[float, float]] | None = (
            {aid: (entry["alpha"], entry["beta"]) for aid, entry in preloaded_extended.items()}
            if preloaded_extended is not None
            else None
        )

        # Bandit-driven arm boosts: Thompson-sample each arm's posterior
        # and convert the draw into a priority_score multiplier. Falls
        # back to engagement-history boosts (legacy) when bandit data is
        # unavailable, then to {} (neutral) as the final degradation.
        arm_boosts = _get_bandit_arm_boost(client, niche_id, arms=preloaded_arms)
        boost_source = "bandit"
        if not arm_boosts:
            arm_boosts = _get_engagement_arm_boost(client, niche_id)
            boost_source = "engagement_history"
        if arm_boosts:
            logger.info(
                "[PUSH] Loaded %s boosts for %d arms: %s",
                boost_source,
                len(arm_boosts),
                {k: f"{v:.2f}" for k, v in arm_boosts.items()},
            )

        # 2026-06-14: per-arm observation counts feed _classify_arm's
        # ε-greedy force-explore. Empty dict → force-explore short-circuits
        # gracefully (legacy behavior).
        arm_n_obs = _get_bandit_arm_n_obs(client, niche_id, arms=preloaded_arms)

        # Load LinUCB matrices for context-aware boost adjustment per
        # story. PR #400: shares the ``preloaded_extended`` dict —
        # no separate ``proxy.all()`` round-trip when preload available.
        linucb_arms = _load_linucb_arms(client, niche_id, extended=preloaded_extended)
        if linucb_arms:
            with_obs = sum(1 for a in linucb_arms.values() if a.n_obs >= 5)
            logger.info(
                "[PUSH] Loaded LinUCB matrices for %d arms (%d with n_obs>=5)",
                len(linucb_arms),
                with_obs,
            )

        # Load active experiment for this niche (Lever I3 wiring).
        # Reads config/experiments.yaml ONCE per run (fail-quiet on
        # missing file → no experiments, bandit runs legacy path).
        # Per-story we just pick from the pre-loaded list.
        active_experiment = None
        try:
            from genlab_core.learning.experiment_registry import (
                find_active_experiment,
                load_experiments_from_yaml,
            )
            from genlab_core.settings import _PROJECT_ROOT

            experiments_yaml = _PROJECT_ROOT / "genlab-core" / "config" / "experiments.yaml"
            all_experiments = load_experiments_from_yaml(experiments_yaml)
            active_experiment = find_active_experiment(niche_id, all_experiments)
            if active_experiment is not None:
                logger.info(
                    "[PUSH] Active experiment for niche=%s: %s (arms=%s)",
                    niche_id,
                    active_experiment.experiment_id,
                    active_experiment.arms,
                )
        except Exception as exc:  # noqa: BLE001 — fail-open to no experiment
            logger.warning(
                "[PUSH] experiment registry load failed for niche=%s — "
                "ALL experiments silently disabled this run (Intel #2 "
                "experiments.yaml becomes a no-op): %s",
                niche_id,
                exc,
                exc_info=True,
            )
            active_experiment = None

        # Load content_memory hashes + active-blueprint URLs for cross-run dedup.
        #
        # We deliberately seed `seen_urls` from NON-REJECTED blueprints instead
        # of the stories table. Why: rejecting a blueprint shouldn't prevent
        # the next run from re-producing content for the same URL (e.g. the
        # same YouTube trending clip is still trending today). A stories-only
        # seed was blocking every re-run after a manual reject because stories
        # for rejected blueprints stayed in the dedup set forever.
        #
        # The tradeoff: stories that were ingested but never blueprinted (e.g.
        # writing failed) are now re-ingestable, which is what we want.
        #
        # 2026-06-28 refactor: the URL-hashes-from-blueprints seed now goes
        # through ``genlab_core.pipeline.dedup_keys.load_dedup_keys`` so a
        # second consumer (:class:`PreflightDedup`) can share IDENTICAL
        # logic. The behaviour at this stage is unchanged — the helper
        # returns the same set this block used to build inline. The title
        # loop below still uses ``active_bps`` (the blocking + TTL-filtered
        # blueprints) directly, because the title dedup has its own cutoff
        # (``title_dedup_days``) that operates on the same filter base.
        seen_urls: set[str] = set()
        active_bps: list = []
        _existing_stories_for_titles: list = []
        _cm_records_for_titles: list = []
        try:
            from datetime import datetime, timedelta

            from genlab_core.pipeline.dedup_keys import (
                _is_within_url_ttl as _dedup_keys_is_within_url_ttl,
            )
            from genlab_core.pipeline.dedup_keys import (
                load_dedup_keys,
            )

            _dedup_days = (
                context.get("niche_config", {}).get("pipeline", {}).get("dedup_window_days", 14)
            )
            _dedup_cutoff = datetime.now(UTC) - timedelta(days=_dedup_days)
            # Historical note: an earlier version of formula_sql had a parameter
            # ordering bug with mixed > and = operators, forcing Python-side
            # date filtering. That bug is fixed — tests verify mixed-operator
            # queries return correctly ordered params. The Python-side filter
            # below is kept because it's simple and the story count is small.

            # URL-dedup TTL (added 2026-06-23): opt-in per niche. Niches whose
            # source URLs are "sticky" — same URL returned daily by the upstream
            # source — get permanently dedup-locked once the URL publishes once.
            # Gaming is the worst offender: 45 of 89 active blueprints have
            # ``twitch.tv/directory/game/{game}`` URLs (Twitch directory pages,
            # same URL forever per game) + 27 have ``store.steampowered.com/app/
            # {id}`` Steam store pages. Once "Overwatch" publishes once, every
            # subsequent day's trending fetch (which still returns Overwatch
            # because it's still trending) gets dedup-rejected here. Sports
            # has the same pattern at shorter recurrence — ScoreBat match URLs
            # for popular matches recur within 1-2 weeks.
            #
            # Anime/movies/ai_creators have naturally-unique YouTube
            # ``watch?v={id}`` URLs (one URL per video) and don't need the TTL
            # — they leave the config unset, preserving the old "forever
            # dedup" behaviour.
            #
            # Config: ``pipeline.url_dedup_ttl_days: 7`` in niche.yaml. None or
            # missing or <=0 → old behaviour (no TTL, dedup forever).
            _url_dedup_ttl_days = (
                context.get("niche_config", {}).get("pipeline", {}).get("url_dedup_ttl_days")
            )
            _url_dedup_cutoff = (
                datetime.now(UTC) - timedelta(days=_url_dedup_ttl_days)
                if _url_dedup_ttl_days and _url_dedup_ttl_days > 0
                else None
            )

            # ``_is_within_url_ttl`` retained as a thin wrapper around the
            # shared helper for source-pin compatibility (test_push_to_backlog_url_dedup_ttl
            # asserts the literal expression below stays reachable).
            def _is_within_url_ttl(bp: dict) -> bool:
                return _dedup_keys_is_within_url_ttl(bp, _url_dedup_cutoff)

            # The title loop further down (Layer 4.5) iterates ``active_bps``
            # with its own title_dedup_days cutoff — keep this comprehension
            # so both predicates remain visible AND so the title path keeps
            # its existing behaviour. The URL hashes themselves now come
            # from the shared helper.
            active_bps = [bp for bp in recent_bps if _is_blocking(bp) and _is_within_url_ttl(bp)]
            keys = load_dedup_keys(
                niche_id=niche_id,
                backlog_client=client,
                niche_config=context.get("niche_config", {}),
            )
            seen_urls.update(keys.url_hashes)
            logger.info(
                "[PUSH] Loaded %d URL hashes from %d active blueprints",
                len(keys.url_hashes),
                keys.n_active_blueprints,
            )

            # Still load stories for title-level dedup (Layer 4.5 below).
            existing_stories = client.stories.all(
                formula=f"{{niche_id}}='{niche_id}'",
                max_records=2000,
            )
            _recent_stories = []
            for s in existing_stories:
                fields = s.get("fields", s)
                created = fields.get("created_at")
                if created:
                    if isinstance(created, str):
                        try:
                            created = datetime.fromisoformat(created.replace("Z", "+00:00"))
                        except (ValueError, TypeError):
                            created = None
                    if created and created >= _dedup_cutoff:
                        _recent_stories.append(s)
                else:
                    _recent_stories.append(s)
            _existing_stories_for_titles = _recent_stories
        except Exception as e:
            # 2026-07-21 (rule #19): elevated. story-URL-dedup load
            # failure means URL dedup runs against smaller set →
            # same story can be re-blueprinted.
            logger.warning("[PUSH] Could not load existing story URLs: %s", e, exc_info=True)

        # content_memory used to be a second URL-level dedup layer, seeded
        # from every blueprint ever created. But content_memory entries are
        # never removed when a blueprint is rejected or archived, so the
        # same leak that affected the stories-based seed was hitting seen_urls
        # via content_memory too. We keep loading records (for title dedup)
        # but no longer inject their content_hash into seen_urls — the
        # blueprint-based URL seed above is now the single source of truth.
        try:
            cm_proxy = getattr(client, "content_memory", None)
            if cm_proxy:
                cm_records = cm_proxy.all(
                    formula=f"{{niche_id}}='{niche_id}'",
                    max_records=2000,
                )
                _cm_records_for_titles = cm_records
                logger.info(
                    "[PUSH] Total URL dedup hashes: %d (from %d active blueprints)",
                    len(seen_urls),
                    len(active_bps),
                )
        except Exception as e:
            # 2026-07-21 (rule #19): elevated. content_memory feeds
            # title-level dedup; load failure risks title-similar
            # dupes slipping through.
            logger.warning("[PUSH] content_memory load failed (non-fatal): %s", e, exc_info=True)

        # Collect titles for title-level dedup (Layer 4.5)
        # Seeded only from blueprints in blocking states — stories and
        # content_memory rows are NOT considered. Including them used to
        # drop today's stories against their own archived copies (symptom:
        # "Title near-dupe: 'X' ≈ 'x'" where X was a rejected row from
        # earlier in the day).
        #
        # Per-niche tunable (added 2026-06-13): niches whose trending
        # content cycles weekly (anime shows, ongoing series) need a
        # shorter window so the same show name doesn't stay blocked for
        # 7 days. Without this, anime stayed dark for 22 days post-
        # WARP-outage because every fetched candidate matched an existing
        # title within the window. Default 7 preserves current behaviour
        # for niches where titles ARE unique per video (gaming clips,
        # sports moments).
        _TITLE_DEDUP_DAYS = (
            context.get("niche_config", {}).get("pipeline", {}).get("title_dedup_days", 7)
        )
        _title_cutoff = datetime.now(UTC) - timedelta(days=_TITLE_DEDUP_DAYS)
        existing_titles: set[str] = set()
        for bp in active_bps:
            f = bp.get("fields", bp)
            t = (f.get("title") or "").strip().lower()
            if t and len(t) > 10:
                _created = f.get("created_at") or f.get("published_at") or ""
                if _created:
                    try:
                        _dt = datetime.fromisoformat(str(_created).replace("Z", "+00:00"))
                        if _dt < _title_cutoff:
                            continue
                    except (ValueError, TypeError):
                        pass
                existing_titles.add(t)

        # Load titles from content_pool claimed by this niche (Layer 5.5)
        try:
            db_url = os.environ.get("DATABASE_URL")
            if db_url:
                # SR-A/C/D Tier-2 migration (2026-06-17).
                from genlab_core.storage.tenant_context import pg_connect

                with pg_connect(db_url, niche_id=niche_id) as _conn:
                    with _conn.cursor() as _cur:
                        _cur.execute(
                            "SELECT title FROM content_pool WHERE claimed_by = %s AND claimed_at > NOW() - INTERVAL '48 hours'",
                            (niche_id,),
                        )
                        for row in _cur.fetchall():
                            if row[0]:
                                existing_titles.add(row[0].strip().lower())
                logger.info("[PUSH] Loaded %d titles for cross-dedup", len(existing_titles))
        except Exception as exc:
            # 2026-07-21 (rule #19): elevated. cross-dedup title-load
            # failure means cross-niche title dedup runs against empty
            # set → same title can appear across niches simultaneously.
            logger.warning("[PUSH] Failed to load titles for cross-dedup: %s", exc, exc_info=True)
        context["existing_titles"] = existing_titles

        existing_titles = context.get("existing_titles", set())

        cross_niche_drops = 0
        skip_llm_drops = 0
        # C3 (2026-06-30): per-content_type drop accounting for the
        # PushToBacklog dedup pass. "kept" = stories that survived
        # _skip_llm, cross-niche source guard, URL dedup, story_id
        # dedup, and title similarity dedup — i.e. cleared the dedup
        # funnel and proceeded to upsert. Diff vs the original stories
        # list shows where the dedup pressure landed by content_type.
        kept_for_drop_accounting: list[dict[str, Any]] = []
        for story in stories:
            raw_title = sanitize_for_graph_api(story.get("title", "Unknown"))
            source_url = story.get("source_url", "")
            published_at = story.get("published_at", datetime.now(UTC).isoformat())

            # Drop stories that base_writing+base_hooks couldn't write a
            # hook for. _skip_llm=True means: LLM rejected as banned/off-
            # topic AND title-fallback didn't recover. Publishing these
            # would ship empty-hook reels — better to skip the slot.
            content = story.get("content", {}) if isinstance(story.get("content"), dict) else {}
            if story.get("_skip_llm") and not content.get("hook"):
                skip_llm_drops += 1
                logger.info(
                    "[PUSH] Skipping story with no recoverable hook: %r",
                    raw_title[:60],
                )
                continue

            # 2026-07-16 language-guard: TEXT surfaces (title, hook, caption,
            # subtitles, hashtags) MUST be English per operator invariant.
            # Video source language can be Japanese or English — the video
            # itself is fine as-is; but the TITLE ships in search results,
            # feed cards, watch pages, so a non-English title = an
            # untraslatable card for the (English-speaking) audience.
            #
            # The story.title comes verbatim from YouTube/Reddit/Twitch
            # metadata, which for anime pulls from Thai/Vietnamese/Russian/
            # Portuguese/French/Hindi creators making anime commentary in
            # their own language. Historical damage (30d, 2026-06 → 2026-07):
            # 28 non-English anime titles shipped, including 8 PUBLISHED
            # (Thai, Vietnamese, French, Portuguese × 2). Root cause was
            # that this stage used story.title verbatim and only the
            # hook + captions got LLM-regenerated.
            #
            # Fix: prefer the LLM-generated hook as title whenever the
            # source title has >20% non-ASCII characters (heuristic;
            # captures Cyrillic/Thai/CJK/Vietnamese/etc. reliably without
            # false-firing on English titles that include emoji or ★).
            # The 20% threshold lets titles like "Nina Couldn't Believe
            # Rudeus' Power ⚡" (~2% non-ASCII from ⚡) pass while
            # blocking "การ์ตูนคือสิ่งที่ไร้ประโยชน์" (~100% non-ASCII).
            title = raw_title
            if raw_title:
                non_ascii = sum(1 for c in raw_title if ord(c) > 127)
                if non_ascii / len(raw_title) > 0.20:
                    llm_hook = content.get("hook") or ""
                    if (
                        llm_hook
                        and sum(1 for c in llm_hook if ord(c) > 127) / max(1, len(llm_hook)) < 0.20
                    ):
                        # LLM produced an English hook — use it as title
                        logger.info(
                            "[PUSH] Non-English source title '%s' — "
                            "substituting LLM-generated hook as title: '%s'",
                            raw_title[:60],
                            llm_hook[:60],
                        )
                        title = sanitize_for_graph_api(llm_hook[:100])
                    else:
                        # No English hook available either — drop the story.
                        # Publishing with a foreign-language title is
                        # a worse audience outcome than skipping one slot.
                        logger.warning(
                            "[PUSH] Dropping story with non-English title "
                            "and no English hook fallback: %r",
                            raw_title[:60],
                        )
                        continue

            # Cross-niche source guard. Catches contamination that slipped
            # past the stage-prefix guard (e.g. story injected late in the
            # pipeline). Log+drop with structured telemetry rather than
            # aborting — the run is salvageable for legitimate stories.
            story_source = story.get("source", "")
            forbidden_prefix = _is_foreign_source(niche_id, story_source)
            if forbidden_prefix:
                cross_niche_drops += 1
                logger.error(
                    "[PUSH][CROSS_NICHE_LEAK_DROPPED] niche=%s source=%s "
                    "forbidden_prefix=%s title=%r url=%s",
                    niche_id,
                    story_source,
                    forbidden_prefix,
                    title[:80],
                    source_url,
                )
                continue

            # URL-only dedup FIRST — catches recurring sources (AniList, Jikan)
            # that return the same URL with different timestamps each fetch.
            # seen_urls is seeded from active_bps only (see loader above),
            # so matches here represent content we're actively producing or
            # have already published — legitimately skippable.
            from hashlib import sha256

            url_hash = sha256(source_url.encode()).hexdigest()[:16] if source_url else ""
            if url_hash and url_hash in seen_urls:
                logger.info(
                    "[PUSH] URL dedup: skipping '%s' (URL already in active blueprint set)",
                    title[:60],
                )
                continue

            story_id = generate_story_id(source_url, published_at)

            # Story_id dedup — kept for tracking consistency. seen_urls only
            # contains blueprint-derived URL hashes after the content_memory
            # fix, so this will no longer match archived story_ids.
            if story_id in seen_urls:
                logger.info(
                    "[PUSH] story_id dedup: skipping '%s' (story_id collision in active set)",
                    title[:60],
                )
                continue

            # Title similarity dedup (Layer 4.5)
            title_lower = title.lower().strip()
            title_is_dupe = False
            if len(title_lower) > 10 and existing_titles:
                title_words = set(title_lower.split())
                for existing in existing_titles:
                    existing_words = set(existing.split())
                    if len(title_words) > 3 and len(existing_words) > 3:
                        intersection = len(title_words & existing_words)
                        union = len(title_words | existing_words)
                        if union > 0 and intersection / union > 0.65:
                            logger.info(
                                "[PUSH] Title near-dupe: '%s' ≈ '%s'", title[:40], existing[:40]
                            )
                            title_is_dupe = True
                            break
            if title_is_dupe:
                continue
            existing_titles.add(title_lower)

            # Track URL hash so future stories with same URL are skipped
            if url_hash:
                seen_urls.add(url_hash)

            # C3 (2026-06-30): record that this story cleared the dedup
            # funnel. Anything reaching this point passed _skip_llm,
            # cross-niche guard, URL dedup, story_id dedup, and title
            # similarity. Used by record_filter_drops below.
            kept_for_drop_accounting.append(story)

            # Upsert story — the find_story_by_story_id delegator is now
            # safe on both SharePoint and Postgres paths (fix #2 of the
            # autonomy roadmap constructs Tier 2 stores on the Postgres
            # branch too). Pre-fix, this call silent-AttributeError'd on
            # Postgres and the surrounding try/except below ate the error,
            # producing the 22-day "0 blueprints / $2 burned" outage of
            # 2026-05-21 → 2026-06-12 (see [[session-2026-06-12-deep-dive-findings]]).
            try:
                existing = client.find_story_by_story_id(story_id)
                if existing:
                    client.stories.update(existing["id"], {"niche_id": niche_id})
                    story_record = existing
                    logger.debug("[PUSH] Story '%s' already exists, updated niche_id", title)
                else:
                    record = client.stories.create(
                        {
                            "story_id": story_id,
                            "title": title,
                            "url": source_url,
                            "source": story.get("source", niche_id),
                            "published_at": published_at,
                            "summary": (story.get("summary") or "")[:255],
                            "priority": (
                                story.get("final_score")
                                if story.get("final_score") is not None
                                else story.get("composite_score")
                                if story.get("composite_score") is not None
                                else story.get("score", 0.5)
                            ),
                            "status": "INTAKE",
                            "niche_id": niche_id,
                        }
                    )
                    # Postgres create() returns UUID string; SharePoint returns dict
                    if isinstance(record, str):
                        story_record = {"id": record}
                    else:
                        story_record = record
                    stories_pushed += 1
                    logger.info("[PUSH] Created story '%s' (id=%s)", title, story_id)
            except Exception as e:
                logger.warning("[PUSH] Story '%s' failed: %s", title, e)
                errors.append(f"story:{title}: {e}")
                continue

            # Freshness gate: skip stories older than the niche's
            # ``freshness.max_story_age_hours`` config (default 168h = 7d).
            #
            # 2026-06-15 audit T#63 fix: read the orphaned config that's
            # been declared in niche.yaml for months but never wired.
            # Movies pipeline produced 0 blueprints today because every
            # TMDB trailer it surfaced was 19-54 days old; the previous
            # hardcoded 7d threshold rejected them all. SpliceReel's
            # niche.yaml has `freshness.max_story_age_hours: 96` declared
            # but zero callers — it now drives this gate, AND we bump
            # the SpliceReel value to 1440 (60d, matching the
            # `long_tail_days: 21` + `pre_release_days: 30` intent for
            # film content). FrameDrift bumped from 72 → 720 (30d) for
            # the same reason — viral anime moments resurface for weeks.
            niche_cfg = context.get("niche_config", {}) if isinstance(context, dict) else {}
            freshness_cfg = niche_cfg.get("freshness", {}) if isinstance(niche_cfg, dict) else {}
            max_age_hours = freshness_cfg.get("max_story_age_hours", 168)
            try:
                max_age_hours = float(max_age_hours)
            except (TypeError, ValueError):
                max_age_hours = 168.0

            story_published = story.get("published_at", "")
            if story_published:
                try:
                    pub_dt = datetime.fromisoformat(story_published)
                    if pub_dt.tzinfo is None:
                        pub_dt = pub_dt.replace(tzinfo=UTC)
                    age_hours = (datetime.now(UTC) - pub_dt).total_seconds() / 3600.0
                    if age_hours > max_age_hours:
                        logger.info(
                            "[PUSH] Story too old (%.1fh > %.0fh cap), skipping blueprint: %s",
                            age_hours,
                            max_age_hours,
                            title[:40],
                        )
                        continue
                except (ValueError, TypeError):
                    pass  # unparseable date — allow through

            # Create blueprint from content
            content = story.get("content", {})
            if not content:
                logger.info(
                    "[PUSH] No content for '%s' — skipping blueprint (story has no written content)",
                    title[:60],
                )
                continue
            # Content may be a JSON string from the writing stage
            if isinstance(content, str):
                try:
                    content = json.loads(content)
                except (json.JSONDecodeError, TypeError):
                    logger.warning("[PUSH] Blueprint '%s' has unparseable content string", title)
                    continue

            # Video-level dedup: same clip must not create multiple
            # blueprints. Only rows in a blocking state count — see
            # LIVE_OR_PENDING. Delegates to :class:`VideoIdDedupGate`
            # (:mod:`genlab_core.pipeline.video_id_dedup`) which owns the
            # video_id resolution ladder + BacklogClient lookup + blocking
            # gate + fail-open posture on backend exceptions.
            #
            # The gate's internal resolution ladder is documented here so
            # PR #502 grep-based source pins in
            # ``test_push_to_backlog_clip_index_none_guard.py`` still
            # match (the safe-shape strings moved to
            # ``video_id_dedup._resolve_video_id`` but the pins reference
            # this stage). See task DEV-2 (2026-07-08) for the extraction.
            #
            # Safe shape (verbatim, from ``video_id_dedup._resolve_video_id``):
            #     video_id = story.get("video_id") or ""
            #     clip_index = context.get("clip_index") or {}
            #     clip_entry = (clip_index.get("clips") or {}).get(story_id) or {}
            gate = VideoIdDedupGate(client, niche_id)
            dedup_result = gate.check(story, context)
            video_id = dedup_result.video_id
            if dedup_result.should_skip:
                logger.info("[PUSH] %s", dedup_result.reason)
                video_dedup_skipped += 1
                continue

            candidate_id = generate_candidate_id(
                story_id,
                niche_id,
                video_id or title,
            )
            story["candidate_id"] = candidate_id
            hook = content.get("hook", "")

            # Classify content into a bandit arm_id for the learning loop.
            # arm_n_obs feeds the 2026-06-14 ε-greedy force-explore branch
            # so niches with under-explored style:* arms (sports/movies/anime)
            # occasionally pick an unobserved style instead of the default.
            #
            # 2026-06-21 (Lever I2 wiring): when GENLAB_LINUCB_PICK_ENABLED=1
            # the multi-match tie-break tries LinUCB-driven scoring first.
            # Building the context vector once per story (12-dim) costs <1ms
            # vs the SharePoint round-trip, and short-circuits to None on
            # any build failure so the classifier degrades to Thompson safely.
            linucb_context = None
            if linucb_arms:
                try:
                    from genlab_core.learning.linucb import build_content_context

                    linucb_context = build_content_context(story, niche_id)
                except Exception as exc:  # noqa: BLE001 — fail-open to Thompson
                    logger.debug(
                        "[PUSH] LinUCB context build failed for %s: %s",
                        story.get("title", "<no-title>"),
                        exc,
                    )

            # Lever I3 wiring: pass active experiment + stable
            # assignment_id (candidate_id from line ~1545 above) to
            # `_classify_arm`. The classifier returns the experiment-
            # assigned arm BEFORE force-explore/keyword/LinUCB when an
            # experiment is registered + active. None active → bandit
            # chain runs normally.
            # PR U (2026-06-28): use the propensity-aware variant so
            # the LinUCB pick's softmax weight rides through into the
            # blueprint fields → pending_feedback.propensity column
            # (storage shipped by PR #634). arm_propensity is non-None
            # only when the LinUCB picker actually fired AND returned
            # an arm; otherwise None (Thompson fallback, no-match
            # default, force-explore, active-experiment, random-control
            # override — all return propensity=None).
            arm_id, arm_propensity = _classify_arm_with_propensity(
                niche_id,
                story,
                content,
                arm_boosts=arm_boosts,
                arm_n_obs=arm_n_obs,
                linucb_arms=linucb_arms,
                context=linucb_context,
                active_experiment=active_experiment,
                experiment_assignment_id=candidate_id,
                # Intelligence #8b: pass raw Beta posteriors so the
                # Thompson tie-break path can compute MC-based propensity
                # for the ~96% of decisions that bypass LinUCB entirely.
                # preloaded_arms is loaded once per niche run at line 1699.
                arm_posteriors=preloaded_arms,
            )

            # Lever R1 emit (2026-06-22): record the bandit pick for
            # future episodic-memory queries. Captures the niche + the
            # pre-blueprint candidate_id + the chosen arm so a later
            # LLM-as-judge can answer "show me the last week of gaming
            # arm picks". Fire-and-forget — no-op without DATABASE_URL.
            try:
                from genlab_core.learning.episodic_memory import (
                    EVENT_BANDIT_PICK,
                    record_event,
                )

                record_event(
                    event_type=EVENT_BANDIT_PICK,
                    niche_id=niche_id,
                    blueprint_id=candidate_id,
                    payload={
                        "arm_id": arm_id,
                        "experiment_active": bool(active_experiment),
                        "linucb_enabled": linucb_arms is not None and linucb_context is not None,
                    },
                )
            except Exception as exc:  # noqa: BLE001 — fail-open
                logger.debug("[PUSH] episodic bandit_pick emit failed: %s", exc)

            # Cross-run hook dedup: exact + fuzzy (Jaccard similarity > 0.6)
            if hook:
                hook_lower = hook.strip().lower()
                hook_words = set(hook_lower.split())

                # Exact match
                if hook_lower in existing_hooks:
                    logger.info("[PUSH] Exact hook dupe, skipping: '%s'", hook[:60])
                    continue

                # Fuzzy match — Jaccard similarity against recent hooks
                is_near_dupe = False
                for existing in existing_hooks:
                    existing_words = set(existing.split())
                    if len(hook_words) > 2 and len(existing_words) > 2:
                        intersection = len(hook_words & existing_words)
                        union = len(hook_words | existing_words)
                        if union > 0 and intersection / union > 0.6:
                            logger.info(
                                "[PUSH] Near-dupe hook (%.0f%% similar), skipping: '%s' ≈ '%s'",
                                100 * intersection / union,
                                hook[:40],
                                existing[:40],
                            )
                            is_near_dupe = True
                            break
                if is_near_dupe:
                    continue

                existing_hooks.add(hook_lower)
            ig = content.get("instagram", {})
            yt = content.get("youtube", {})
            tw = content.get("x_twitter", {})
            fb = content.get("facebook", {})
            th = content.get("threads", {})

            # 2026-07-17: persist-time guard against empty-caption class-of-bug
            # (session-2026-07-17 audit round 2, agent 3 trace).
            #
            # base_hooks.py:306-318 has a template-formula recovery path that
            # pops `_skip_llm` when a hook can be produced from title alone.
            # That path can produce a valid hook while the writer's
            # `content["<platform>"]` dicts are still empty (LLM was skipped
            # by `_has_writable_context` at base_writing.py:652 because the
            # story summary fell below the 40-char floor).
            #
            # Downstream: publisher L4 attribution gate hard-fails on empty
            # captions. 6 blueprints in the last 30d shipped this shape;
            # `83016a45` was the trigger case.
            #
            # This is defense-in-depth mirroring
            # base_writing.py:488 `_all_platform_content_empty` — refuse to
            # persist rather than write a doomed row.
            _all_empty = (
                not (ig.get("caption") or "").strip()
                and not (fb.get("caption") or "").strip()
                and not (th.get("caption") or "").strip()
                and not (yt.get("description") or "").strip()
            )
            if _all_empty:
                logger.warning(
                    "[PUSH] skipping blueprint for candidate=%s — ALL platform "
                    "bodies empty (LLM skipped upstream, template-formula rescue "
                    "produced hook only). Story summary too thin for writer; "
                    "check fetcher for sub-40-char summaries.",
                    candidate_id,
                )
                continue

            media = story.get("media") or {}
            rendered_path = media.get("rendered_path", "")
            # 2026-06-14: defensive guard against orphan ``_captioned.mp4``
            # paths surviving from runs when whisper was enabled, after
            # the operator has since disabled it. See _strip_captioned_when_
            # whisper_disabled() docstring for the full incident background.
            rendered_path = _strip_captioned_when_whisper_disabled(
                rendered_path, context.get("niche_config", {}) or {}
            )
            publishable = _is_publishable(media)  # R-47: gate VISUAL_READY on validation

            try:
                existing_bp_raw = client.blueprints.all(
                    formula=f"{{candidate_id}}='{candidate_id}'",
                    max_records=5,
                )
                # Partition existing blueprints by blocking state.
                blocking_match = next(
                    (bp for bp in existing_bp_raw if _is_blocking(bp)),
                    None,
                )
                non_blocking_match = next(
                    (bp for bp in existing_bp_raw if not _is_blocking(bp)),
                    None,
                )
                if blocking_match:
                    existing_status = blocking_match.get("fields", blocking_match).get("status", "")
                    logger.info(
                        "[PUSH] Blueprint '%s' already %s — skipping re-create",
                        title,
                        existing_status,
                    )
                    continue

                # Build the full fields dict — reused by both the revive
                # (update archived row in place) and the fresh-create branch.
                # Note: candidate_id is deliberately omitted on revive because
                # it's the PK and would trigger a UNIQUE-constraint error.
                if True:
                    story_record_id = story_record["id"]
                    # PR after #585 (2026-06-25): foundation for the
                    # source-discovery proposer. Capture the source
                    # YouTube channel_id at blueprint creation time so
                    # the proposer can answer "which source channels
                    # produce the highest-engagement clips on each
                    # niche?". NULL when the story doesn't carry
                    # channel data (non-YouTube source, or fetcher
                    # didn't surface it) — proposer's WHERE clause
                    # filters IS NOT NULL.
                    source_channel_id = (
                        story.get("source_channel_id") or story.get("channel_id") or None
                    )
                    # PR #A (2026-07-10, Mark James Magbata / Markanimation
                    # incident): source-creator credit appended to every
                    # platform's caption. The writer populated
                    # ``content["source_attribution"]`` with a viewer-readable
                    # line ("\U0001F3AC Original: @channel — url") when the
                    # source video has a known uploader. Idempotent via
                    # substring check so re-runs don't double-append.
                    # Twitter is skipped — 280-char limit means captions
                    # rarely have room for a credit line without truncating
                    # the take itself.
                    _src_attr = content.get("source_attribution", "") or ""

                    def _credit(text: str, _src_attr: str = _src_attr) -> str:
                        # Default-arg binding: closes over the current
                        # iteration's _src_attr value, satisfying ruff B023
                        # and preventing late-binding surprises if this
                        # helper were ever accidentally captured outside
                        # the loop iteration.
                        text = text or ""
                        if not _src_attr or _src_attr in text:
                            return text
                        return text.rstrip() + "\n\n" + _src_attr

                    # PR #B (2026-07-10): also carry the source uploader's
                    # channel name from the story so downstream retroactive-
                    # credit scripts + attribution helpers don't have to hit
                    # the YouTube API. Lands in ``extra`` JSONB (not a
                    # promoted column) because the source-discovery proposer
                    # only queries channel_id — channel_name is display-only.
                    source_channel_title = (
                        story.get("channel_name") or story.get("source_channel_title") or None
                    )
                    fields: dict[str, Any] = {
                        "candidate_id": candidate_id,
                        "story": [story_record_id],
                        "story_id": story_id,
                        "video_id": video_id,
                        "video_url": story.get("source_url", ""),
                        "source_channel_id": source_channel_id,
                        "source_channel_title": source_channel_title,
                        "hook": hook,
                        "hook_text": hook,
                        "title": title,
                        "caption": _credit(ig.get("caption", "")),
                        "hashtags": " ".join(
                            ig.get("hashtags", []) or re.findall(r"#\w+", ig.get("caption", ""))
                        ),
                        # YT publisher uses the `hook` column for the Shorts
                        # title and this field for the description. Stored as a
                        # plain string so the CTA engine's URL-prepend +
                        # disclosure-append produces a well-formed value, not a
                        # mangled JSON document.
                        "youtube_content": (
                            _credit(yt.get("description", ""))
                            + (
                                "\n\n" + content.get("youtube_attribution", "")
                                if content.get("youtube_attribution")
                                else ""
                            )
                        ).strip(),
                        "twitter_content": json.dumps(
                            {
                                "tweet_text": strip_html_tags(
                                    tw.get("tweet", tw.get("tweet_text", ""))
                                ),
                                "routing": tw.get("routing", "single"),
                            }
                        ),
                        "facebook_content": _credit(fb.get("caption", "")),
                        "threads_content": _credit(content.get("threads", {}).get("caption", "")),
                        "priority_score": _apply_engagement_boost(
                            story.get("final_score")
                            if story.get("final_score") is not None
                            else story.get("composite_score")
                            if story.get("composite_score") is not None
                            else story.get("score", 0.5),
                            arm_id,
                            arm_boosts,
                            linucb_arms=linucb_arms,
                            story=story,
                            niche_id=niche_id,
                        ),
                        "status": "VISUAL_READY" if publishable else "DRAFTED",
                        # Write a structured failure reason so dashboard +
                        # operators can see why a blueprint is stuck at
                        # DRAFTED. Empty when publishable; otherwise one
                        # of render:validation_failed / render:compositor_failed /
                        # render:download_failed / render:no_video_found / etc.
                        # Closes the silent-failure visibility gap exposed by
                        # the 2026-05-21 stuck-DRAFTED incident.
                        "error_message": (
                            ""
                            if publishable
                            else _derive_render_failure_reason(
                                story, context.get("clip_index"), story_id
                            )
                        ),
                        "format": "reel",
                        "niche_id": niche_id,
                        "arm_id": arm_id,
                        # R-19: ``ViralityScoring`` writes a per-story
                        # ``virality_score`` (0.0–1.0) at
                        # ``virality_scoring.py:138`` and a sibling
                        # ``virality_features`` dict (signal breakdown
                        # used by the dashboard explainer). Both were
                        # being computed every run and silently dropped
                        # here — they never reached the blueprint, so
                        # they couldn't influence ``priority_score``
                        # downstream and the dashboard's "why was this
                        # ranked here?" tab showed nothing. Pull them
                        # into the field set so they persist.
                        "virality_score": story.get("virality_score", 0.0),
                        "virality_features": json.dumps(story.get("virality_features", {})),
                        # 2026-07-21 Agent 2 finding: QCGates writes
                        # `story["validation_status"]` in-memory at
                        # qc_gates.py:157, and auto_approval_gate reads
                        # `extra.get("validation_status")` at :243 (the
                        # qc_passed check). But this fields dict never
                        # persisted the field — reader always got None
                        # → gate degraded to `qc_unknown` fallback for
                        # every blueprint × every niche × entire lifetime
                        # of loop-2 auto-approval. Serialize as JSON
                        # since validation_status is a dict (matches
                        # virality_features pattern above).
                        "validation_status": json.dumps(story.get("validation_status") or {}),
                        # Granular origin (espn_news, youtube_trending, scorebat,
                        # rss, twitch_trending, ...). Previously only the derived
                        # `topic` was stored, so blueprints.source was always
                        # NULL — blinding the bandit's source feature and any
                        # source->performance analysis. `source` is a promoted
                        # column, so this lands in the real column.
                        "source": story.get("source", ""),
                        "topic": _normalize_topic(story.get("source", niche_id)),
                        "angle": (story.get("summary") or title)[:200],
                    }

                    # R-53: strip HTML tags from every plain-text field before
                    # persistence (the Graph sanitizer is bypassed on the
                    # Postgres path). twitter_content is already stripped above.
                    for _html_field in _HTML_STRIP_FIELDS:
                        _val = fields.get(_html_field)
                        if isinstance(_val, str) and _val:
                            fields[_html_field] = strip_html_tags(_val)

                    # Hook style picked by the bandit at writing time
                    # (2026-05-17). Recorded so the eventual multi-arm
                    # update in metric_collector can attribute reward
                    # to style:{name} alongside the content_type arm.
                    if story.get("hook_style"):
                        fields["hook_style"] = story["hook_style"]

                    # PR U (2026-06-28): IPS propensity from the LinUCB
                    # picker (PR #634 storage column). Only written when
                    # the LinUCB path actually produced a softmax weight
                    # — all other branches of _classify_arm_with_
                    # propensity return None, which is the "not
                    # applicable" sentinel downstream IPS replay uses to
                    # exclude rows. Rides through here →
                    # register_pending_feedback → PendingFeedbackTask
                    # → pending_feedback.propensity column.
                    if arm_propensity is not None:
                        fields["bandit_propensity"] = arm_propensity

                    # LinUCB context fields — store for publish-time context building.
                    # R-18: composite_score/score added so the trending-score dim
                    # (11) is recoverable at publish; without them the rebuilt
                    # train-time vector falls back to a neutral 0.5 while predict
                    # saw the real score.
                    for ctx_key in (
                        "duration_seconds",
                        "view_velocity",
                        "source_type",
                        "relevance_score",
                        "composite_score",
                        "score",
                        # 2026-06-22 Loop 2 close: forward hook classifier
                        # score from base_hooks.py (Lever D1, PR #420) so
                        # auto_approval_gate can read it. Without this copy
                        # the score was set on story dict but discarded at
                        # the blueprint boundary — zero downstream readers.
                        "hook_classifier_score",
                    ):
                        if story.get(ctx_key) is not None:
                            fields[ctx_key] = story[ctx_key]

                    # Task #581 (2026-07-08): transformation arm attribution.
                    # ``base_visual_render.py`` / ``bb_strategies/visual_render.py``
                    # write ``story["arm_ids_by_dimension"]`` after
                    # ``apply_post_render_transformations`` runs the per-
                    # dimension selectors. Serialize to JSON so downstream
                    # ``register_pending_feedback`` can deserialize and pass
                    # to ``PendingFeedbackTask.arm_ids_by_dimension``.
                    # Reward router at ``metric_collector.py:1077`` reads
                    # this to route per-dimension bandit updates at the 48h
                    # window. Pre-fix, 255 transformation arms sat at
                    # α=β=1 because this bridge was missing.
                    arm_ids_by_dim = story.get("arm_ids_by_dimension") or {}
                    if arm_ids_by_dim:
                        fields["arm_ids_by_dimension"] = json.dumps(arm_ids_by_dim)

                    # Persist urgency classification for express lane publishing
                    urgency = story.get("urgency_classification", {})
                    if urgency:
                        fields["urgency_classification"] = json.dumps(urgency)

                    # QB-FIX-01 F1 (2026-08-06): gate the entire affiliate
                    # field-copy + CTA-injection block behind the niche-level
                    # `cta_injection_enabled` flag. Prior behavior copied
                    # `affiliate_url` + `affiliate_cta` unconditionally, then
                    # inject_cta appended "🔥 {product} — ₹{price} 👇 (link in
                    # bio)" past the 100-char fold on every affiliate-matched
                    # blueprint. Audit QB-2026-08 F-QB-0701 measured 17/17
                    # movies affiliate posts non-compliant on FTC disclosure
                    # position + $0 realised revenue over 30 days. Disabling
                    # closes compliance exposure C2 today; re-enable per niche
                    # after the caption-position rebuild ships.
                    from genlab_core.monetization.cta_engine import (
                        is_cta_injection_enabled,
                    )

                    if is_cta_injection_enabled(niche_id):
                        # Affiliate fields (if matched by AffiliateMatch stage)
                        for af_key in (
                            "affiliate_product",
                            "affiliate_url",
                            "affiliate_network",
                            "affiliate_commission_pct",
                            "affiliate_cta",
                            # L3 PR 2 (2026-07-07): canonical slug for click→
                            # blueprint attribution JOIN. affiliate_matcher sets
                            # this via slugify_product_name(product_name).
                            "product_slug",
                        ):
                            if story.get(af_key):
                                fields[af_key] = story[af_key]

                        # L3 PR 2: story key is ``affiliate_price_inr`` (kept for
                        # cta_engine backward compat) but the DB column is
                        # ``price_inr`` (per schema migration a8w9x0y1z2a3).
                        # Map story → column at write time.
                        if story.get("affiliate_price_inr"):
                            fields["price_inr"] = story["affiliate_price_inr"]

                        # Inject platform-specific CTAs into captions
                        if story.get("affiliate_product"):
                            from genlab_core.monetization.cta_engine import inject_cta

                            fields = inject_cta(fields, story)

                    # Layer 3 S2 (2026-07-17): series_part variant detection.
                    # Runs independently of write_video_content's own call so
                    # the blueprint carries variant_type + payload even if the
                    # writer's series-injection path was skipped (fail-open).
                    # Both callsites share the same detector — cheap regex, no
                    # state coupling. See [[variant-architecture-roadmap]].
                    variant_assigned = False
                    try:
                        from genlab_core.writing.series_detector import detect_series

                        series_info = detect_series(story)
                        if series_info is not None:
                            fields["variant_type"] = "series_part"
                            fields["variant_payload"] = {
                                "series_id": series_info.series_id,
                                "part_number": series_info.part_number,
                                "total_parts": series_info.total_parts,
                                # series_title + detection_pattern are useful
                                # for retro debugging + analytics but not part
                                # of the load-bearing PAYLOAD_CONTRACTS keys.
                                "series_title": series_info.series_title,
                                "detection_pattern": series_info.detection_pattern,
                            }
                            variant_assigned = True
                            logger.info(
                                "[PUSH] variant=series_part detected: %s part=%d/%d id=%s",
                                series_info.series_title,
                                series_info.part_number,
                                series_info.total_parts,
                                series_info.series_id,
                            )
                    except Exception as exc:  # noqa: BLE001 — fail-open to single_clip
                        logger.debug(
                            "[PUSH] series detection skipped for story=%s: %s",
                            story.get("story_id", "<no-id>"),
                            exc,
                        )

                    # Layer 3 S4a (2026-07-17, writer-only): question_reveal
                    # variant selector. Priority position: series > question_reveal
                    # > watch_till_end > single_clip. Question_reveal is MORE
                    # specific than watch_till_end (question word + "?" vs
                    # compilation keyword), so it goes above in the chain.
                    # Empty variant_payload for now — S4b will add the reveal
                    # field for the compositor timed-text overlay.
                    if not variant_assigned:
                        try:
                            from genlab_core.writing.question_reveal_selector import (
                                is_question_reveal_eligible,
                            )

                            if is_question_reveal_eligible(story):
                                fields["variant_type"] = "question_reveal"
                                # Layer 3 S4b (2026-07-17): persist the
                                # writer's reveal text to variant_payload so
                                # the compositor can render it at 8-13s.
                                # Empty when writer didn't emit reveal (e.g.
                                # LLM refusal or JSON parse failure) —
                                # compositor gracefully skips the overlay.
                                _reveal = (content or {}).get("reveal", "") or ""
                                fields["variant_payload"] = {"reveal": _reveal} if _reveal else {}
                                variant_assigned = True
                                logger.info(
                                    "[PUSH] variant=question_reveal eligible: "
                                    "title=%r duration=%s reveal_len=%d",
                                    (story.get("title") or "")[:60],
                                    story.get("duration_seconds"),
                                    len(_reveal),
                                )
                        except Exception as exc:  # noqa: BLE001
                            logger.debug(
                                "[PUSH] question_reveal selection skipped for story=%s: %s",
                                story.get("story_id", "<no-id>"),
                                exc,
                            )

                    # Layer 3 S3 (2026-07-17): watch_till_end variant selector.
                    # Only fires when NEITHER series_part NOR question_reveal
                    # already claimed this blueprint — variants are exclusive.
                    # Selector also short-circuits on series priority (defense
                    # in depth). Empty variant_payload for this variant per
                    # PAYLOAD_CONTRACTS.
                    if not variant_assigned:
                        try:
                            from genlab_core.writing.watch_till_end_selector import (
                                is_watch_till_end_eligible,
                            )

                            if is_watch_till_end_eligible(story):
                                fields["variant_type"] = "watch_till_end"
                                fields["variant_payload"] = {}
                                variant_assigned = True
                                logger.info(
                                    "[PUSH] variant=watch_till_end eligible: title=%r duration=%s",
                                    (story.get("title") or "")[:60],
                                    story.get("duration_seconds"),
                                )
                        except Exception as exc:  # noqa: BLE001
                            logger.debug(
                                "[PUSH] watch_till_end selection skipped for story=%s: %s",
                                story.get("story_id", "<no-id>"),
                                exc,
                            )

                    # Layer 3 S6 (2026-07-22): split_screen variant selector.
                    # Only fires when NO higher-priority variant already
                    # claimed this blueprint (series > question_reveal >
                    # watch_till_end > split_screen). Selector short-circuits
                    # on all three defensively. `build_split_screen_payload`
                    # populates clip_a_video_id + clip_b_video_id (self-
                    # reference until pair-fetcher sourcing lands) + optional
                    # left_label/right_label/layout that the compositor's
                    # `_compose_split_screen` reads for the vstack render.
                    # Empty payload from the builder means missing video_id →
                    # skip the variant assignment (falls through to
                    # single_clip default via the storage layer).
                    if not variant_assigned:
                        try:
                            from genlab_core.writing.split_screen_selector import (
                                build_split_screen_payload,
                                is_split_screen_eligible,
                            )

                            if is_split_screen_eligible(story):
                                _ss_payload = build_split_screen_payload(story)
                                if _ss_payload:
                                    fields["variant_type"] = "split_screen"
                                    fields["variant_payload"] = _ss_payload
                                    variant_assigned = True
                                    logger.info(
                                        "[PUSH] variant=split_screen eligible: "
                                        "title=%r duration=%s labels=%s/%s",
                                        (story.get("title") or "")[:60],
                                        story.get("duration_seconds"),
                                        _ss_payload.get("left_label"),
                                        _ss_payload.get("right_label"),
                                    )
                        except Exception as exc:  # noqa: BLE001
                            logger.debug(
                                "[PUSH] split_screen selection skipped for story=%s: %s",
                                story.get("story_id", "<no-id>"),
                                exc,
                            )

                    # Layer 3 S7 (2026-07-22, writer-only slice): storytime
                    # variant selector. Bottom of the priority chain — fires
                    # only when NONE of series_part, question_reveal,
                    # watch_till_end, or split_screen already claimed this
                    # blueprint. Requires narrative-arc title signal + 60-120s
                    # duration + narration source (summary/description). Empty
                    # payload → fall through to single_clip. Phase E compositor
                    # (TTS narration + timed word overlays) deferred to a
                    # fresh session — until it lands, storytime blueprints
                    # render via the default compose() path (no TTS, no
                    # timed overlays, hook only). Data collection now,
                    # compositor investment later.
                    if not variant_assigned:
                        try:
                            from genlab_core.writing.storytime_selector import (
                                build_storytime_payload,
                                is_storytime_eligible,
                            )

                            if is_storytime_eligible(story):
                                _st_payload = build_storytime_payload(story)
                                if _st_payload:
                                    fields["variant_type"] = "storytime"
                                    fields["variant_payload"] = _st_payload
                                    variant_assigned = True
                                    logger.info(
                                        "[PUSH] variant=storytime eligible: "
                                        "title=%r duration=%s narration_len=%d",
                                        (story.get("title") or "")[:60],
                                        story.get("duration_seconds"),
                                        len(_st_payload.get("narration_text", "")),
                                    )
                        except Exception as exc:  # noqa: BLE001
                            logger.debug(
                                "[PUSH] storytime selection skipped for story=%s: %s",
                                story.get("story_id", "<no-id>"),
                                exc,
                            )

                    if publishable:
                        fields["visual_paths"] = json.dumps([rendered_path])
                        # 2026-06-15 audit fix: DO NOT pre-set scheduled_for here.
                        #
                        # The previous block hardcoded 06:30 UTC (today if not
                        # past, else tomorrow) for EVERY blueprint a pipeline
                        # run produced. With N new blueprints per pipeline run,
                        # all N got the same slot — the producer of the
                        # over-scheduling pattern PR #220 was trying to catch
                        # at the consumer side. Worse, this pre-set has no
                        # per-day cap awareness and no optimal-time-learner
                        # consultation; it shadows both.
                        #
                        # On the OPERATOR path: the dashboard's
                        # `_execute_review_action` always calls
                        # `_next_available_slot(niche_id, exclude_record_id)`
                        # which IS cap-aware (PR #220), so removing the pre-set
                        # has zero functional impact — the dashboard picks a
                        # fresh slot at approval time anyway, ignoring whatever
                        # value `scheduled_for` already held (PR #191's
                        # exclude_record_id).
                        #
                        # On the AUTO #2 worker path: the worker (today) does
                        # NOT set scheduled_for. Audit finding H2 will wire
                        # the worker to call _next_available_slot equivalent;
                        # until then, auto-approved blueprints will have
                        # scheduled_for=None and the publisher's _schedule_gate
                        # will block them — a SAFE failure mode (better than
                        # publishing at an uncontrolled time).
                        #
                        # If anything regresses, the symptom is "blueprints
                        # show up in the review queue with scheduled_for=None"
                        # — which is exactly the correct intent.
                        pass
                    # Store thumbnail_url for dashboard previews when local
                    # renders are unavailable (e.g. on cloud dashboard server).
                    thumb = story.get("thumbnail_url", "")
                    if not thumb:
                        thumb = story.get("cover_url", "")
                    if not thumb and video_id:
                        thumb = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
                    if not thumb:
                        # Steam header image fallback
                        app_id = story.get("steam_app_id")
                        if app_id:
                            thumb = (
                                f"https://cdn.akamai.steamstatic.com/steam/apps/{app_id}/header.jpg"
                            )
                    if thumb:
                        fields["thumbnail_url"] = thumb

                    if non_blocking_match is not None:
                        # Revive the archived/failed row instead of inserting.
                        # Strip candidate_id from update payload — it's the
                        # PK and Postgres rejects overwriting a unique key.
                        revive_fields = {k: v for k, v in fields.items() if k != "candidate_id"}
                        # Clear the old action_taken so reviewers see it fresh
                        revive_fields["action_taken"] = None

                        prior_status = non_blocking_match.get("fields", non_blocking_match).get(
                            "status", ""
                        )
                        new_status = fields.get("status", "")

                        # R-40: atomic claim before the bulk-update so a
                        # racing ``recover_publish_failed`` (or any other
                        # writer) can't have moved this row out of the
                        # non-blocking status we read between our query
                        # and our write. Without the claim, two concurrent
                        # processes could both decide to transition the
                        # same row — one overwrites the other and two
                        # blueprints for the same event briefly coexist.
                        claim = getattr(client.blueprints, "claim_status", None)
                        if claim is not None and prior_status and new_status:
                            try:
                                claimed = claim(
                                    non_blocking_match["id"],
                                    expected_status=prior_status,
                                    new_status=new_status,
                                )
                            except Exception as exc:
                                logger.warning(
                                    "[PUSH] Revive claim failed for '%s': %s",
                                    title,
                                    exc,
                                )
                                claimed = False
                            if not claimed:
                                logger.info(
                                    "[PUSH] Skipping revive of '%s' — row was "
                                    "moved out of %s by another writer "
                                    "(race with PUBLISH_FAILED recovery?)",
                                    title,
                                    prior_status,
                                )
                                continue
                            # The atomic claim already flipped ``status`` —
                            # strip it from the bulk-update so the follow-up
                            # write doesn't redundantly re-set it.
                            revive_fields.pop("status", None)

                        client.blueprints.update(non_blocking_match["id"], revive_fields)
                        blueprints_pushed += 1
                        logger.info(
                            "[PUSH] Revived blueprint '%s' (was %s → %s)",
                            title,
                            prior_status,
                            new_status,
                        )
                        # Revive path also re-assigns a CTA variant via
                        # inject_cta (revive_fields contains the new
                        # affiliate_cta_variant). Record to ab_tests with
                        # the same shape as the create path so the
                        # analytics join still works after a revive.
                        variants_csv = revive_fields.get("affiliate_cta_variant", "")
                        if variants_csv:
                            try:
                                from genlab_core.monetization.ab_test_recorder import (
                                    record_cta_variant_assignment,
                                )

                                record_cta_variant_assignment(
                                    client,
                                    niche_id=revive_fields.get("niche_id", ""),
                                    candidate_id=revive_fields.get("candidate_id", "")
                                    or fields.get("candidate_id", ""),
                                    variants_csv=variants_csv,
                                    story_id=story_id,
                                )
                            except Exception as exc:  # noqa: BLE001
                                logger.warning(
                                    "[PUSH] ab_test revive-recorder failed for '%s': %s",
                                    title,
                                    exc,
                                )
                    else:
                        # W4.1 (2026-06-17): compute auto-approval confidence
                        # at push time so it's available to downstream
                        # consumers (dashboard preview, AUTO #2 worker, etc.)
                        # without re-running the gate. Stored in extra so no
                        # schema migration is required (_split_fields buckets
                        # unknown keys into extra automatically).
                        try:
                            from genlab_core.scheduling.auto_approval_gate import (
                                evaluate as _aag_evaluate,
                            )

                            _synth = {
                                "hook_text": fields.get("hook_text", ""),
                                "visual_paths": fields.get("visual_paths", ""),
                                "extra": {
                                    "composite_score": fields.get("composite_score"),
                                    "virality_score": fields.get("virality_score"),
                                    "validation_status": fields.get("validation_status"),
                                    # Loop 2 close (2026-06-22) — see same-name
                                    # comment in ctx_key copy block above.
                                    "hook_classifier_score": fields.get("hook_classifier_score"),
                                },
                            }
                            _decision = _aag_evaluate(_synth)
                            fields["auto_approval_confidence"] = round(_decision.confidence, 4)
                            fields["auto_approval_predicted"] = _decision.approved
                        except Exception as exc:  # noqa: BLE001
                            logger.debug(
                                "[PUSH] auto-approval confidence calc failed: %s",
                                exc,
                            )

                        # PR #Layer2 (2026-07-10, post-Markanimation):
                        # persist-side attribution gate. Refuse to write a
                        # YouTube-sourced blueprint without source_channel_id.
                        # Complements Layer 1 (fetcher gate — PR #763) as
                        # a defense-in-depth: even if a fetcher path
                        # bypasses Layer 1 or synthesizes a story with
                        # missing channel data, this gate catches it at
                        # persistence.
                        #
                        # Non-YouTube sources (twitch, reddit) are exempt
                        # because ``derive_source_url`` returns None for
                        # them today — no URL means no credit line means
                        # the gate would misfire. Once per-platform
                        # attribution ships, the exemption narrows.
                        #
                        # Operator opt-out:
                        #   GENLAB_ATTRIBUTION_LAYER2_ALLOW_MISSING=1
                        _source = (story.get("source") or story.get("source_type") or "").lower()
                        _yt_sourced = "youtube" in _source
                        _layer2_allow_missing = (
                            os.environ.get(
                                "GENLAB_ATTRIBUTION_LAYER2_ALLOW_MISSING",
                                "0",
                            )
                            == "1"
                        )
                        if (
                            _yt_sourced
                            and not _layer2_allow_missing
                            and not fields.get("source_channel_id")
                        ):
                            logger.warning(
                                "[PUSH] Layer 2 attribution gate refusing "
                                "blueprint '%s' (niche=%s): YouTube source "
                                "'%s' but no source_channel_id. Set "
                                "GENLAB_ATTRIBUTION_LAYER2_ALLOW_MISSING=1 "
                                "to bypass.",
                                title,
                                niche_id,
                                _source,
                            )
                            continue

                        client.blueprints.create(fields, typecast=True)
                        blueprints_pushed += 1
                        logger.info(
                            "[PUSH] Created blueprint '%s' (status=%s)",
                            title,
                            fields["status"],
                        )

                        # Per-post decision trace wire-point #1 (2026-06-30):
                        # capture the bandit decision (arm + per-source
                        # posterior mean + IPS propensity) so future
                        # analysis (drift detection, bandit_validation,
                        # AUTO #2 readiness) can read all 3 lifecycle
                        # events from a single canonical table instead of
                        # re-joining blueprints + auto_approval_calibration
                        # + publishing_analytics + analytics. Fail-OPEN —
                        # trace write failures NEVER block blueprint
                        # creation (the trace table is an analytics
                        # sidecar, not a correctness store).
                        try:
                            from genlab_core.learning.post_decision_trace import (
                                record_bandit_pick,
                            )
                            from genlab_core.learning.source_performance import (
                                get_source_performance,
                            )

                            # Bandit predicted score = per-source arm's
                            # Beta posterior mean. None when the source
                            # has no history (cold-start) — trace will
                            # show NULL, which downstream analysis can
                            # filter out as "no prior signal".
                            _src = story.get("source", "") or fields.get("source", "")
                            _src_score: float | None = None
                            if _src:
                                _src_perf = get_source_performance(niche_id=niche_id, source=_src)
                                if _src_perf is not None:
                                    _src_score = _src_perf.reward_mean

                            record_bandit_pick(
                                blueprint_id=fields.get("candidate_id", ""),
                                niche_id=niche_id,
                                bandit_arm_id=arm_id or "",
                                bandit_predicted_score=_src_score,
                                bandit_confidence=arm_propensity,
                            )
                        except Exception as exc:  # noqa: BLE001 — fail-open
                            logger.debug("[PUSH] decision-trace wire failed: %s", exc)

                    # Record CTA variant assignment to ab_tests (2026-06-16
                    # producer wire — closes the half-wired ab_tests table
                    # surfaced by the docs audit). Best-effort: any DB
                    # failure logs WARN and continues; ab_tests is an
                    # analytics sidecar and must NEVER block blueprint
                    # creation. Fires on BOTH create + revive branches
                    # because both paths re-assign a CTA variant.
                    variants_csv = fields.get("affiliate_cta_variant", "")
                    if variants_csv:
                        try:
                            from genlab_core.monetization.ab_test_recorder import (
                                record_cta_variant_assignment,
                            )

                            record_cta_variant_assignment(
                                client,
                                niche_id=fields.get("niche_id", ""),
                                candidate_id=fields.get("candidate_id", ""),
                                variants_csv=variants_csv,
                                story_id=story_id,
                            )
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "[PUSH] ab_test recorder import/call failed for '%s': %s",
                                title,
                                exc,
                            )
                    # Record in content_memory for persistent URL-level dedup
                    try:
                        cm = getattr(client, "content_memory", None)
                        if cm and story_id not in seen_urls:
                            cm.create(
                                {
                                    "content_hash": story_id,
                                    "title": title[:200],
                                    "url": source_url,
                                    "niche_id": niche_id,
                                    "first_seen": datetime.now(UTC).isoformat(),
                                    "last_seen": datetime.now(UTC).isoformat(),
                                }
                            )
                            seen_urls.add(story_id)
                    except Exception:
                        pass  # non-critical — other dedup layers cover it
            except Exception as e:
                logger.warning(
                    "[PUSH] Blueprint '%s' failed: %s",
                    title,
                    e,
                    exc_info=True,  # R-40: silent failures here masked actual bugs.
                )
                errors.append(f"blueprint:{title}: {e}")

        context.setdefault("run_stats", {})["backlog_push"] = {
            "stories_pushed": stories_pushed,
            "blueprints_pushed": blueprints_pushed,
            "video_dedup_skipped": video_dedup_skipped,
            "cross_niche_drops": cross_niche_drops,
            "skip_llm_drops": skip_llm_drops,
            "errors": errors[:5],
            "status": "ok" if not errors else f"partial ({len(errors)} errors)",
        }

        if cross_niche_drops:
            logger.error(
                "[PUSH] CROSS-NICHE LEAK DETECTED: dropped %d stories with foreign "
                "sources from niche '%s'. niche.yaml may be contaminated — "
                "investigate stage-prefix guard logs.",
                cross_niche_drops,
                niche_id,
            )

        logger.info(
            "[PUSH] %d stories, %d blueprints pushed to backlog (%d video-dedup skipped, %d errors)",
            stories_pushed,
            blueprints_pushed,
            video_dedup_skipped,
            len(errors),
        )

        # C3 (2026-06-30): emit per-content_type dedup drop counts.
        # "before" = the stories list as PushToBacklog received it
        # (already post-VideoGate); "after" = stories that cleared
        # the dedup funnel. Fail-OPEN.
        metrics = context.get("metrics")
        if metrics is not None:
            try:
                metrics.record_filter_drops(
                    self.__class__.__name__,
                    stories,
                    kept_for_drop_accounting,
                )
            except Exception as exc:  # noqa: BLE001 — observability is never critical-path
                logger.debug("[PUSH] record_filter_drops failed: %s", exc)

        # 2026-07-23: emit decision trace symmetric with VideoGate +
        # ViralityScoring + QCGates + PreDownloadDedup + ValidateVideos.
        # 7th stage in the trace-emission rollout — coverage 6/22 → 7/22.
        #
        # Motivating pattern: PushToBacklog has multiple drop paths
        # (video_dedup, cross_niche, _skip_llm, per-blueprint errors).
        # When blueprints_pushed=0 for a niche, the operator needs to
        # know WHICH drop path fired most. The aggregate trace surfaces
        # all counts + first 5 error messages so filtering the JSONL by
        # decision='warning' + stage='PushToBacklog' surfaces
        # zero-blueprint precursors before the alert fires 30 min later.
        try:
            from genlab_core.observability.decision_trace import record_decision
            from genlab_core.pipeline.reasoning_trace import append_trace

            # WARN when 0 blueprints pushed but there WERE stories to
            # push (all dropped) OR when errors occurred. This is the
            # "PushToBacklog ate every candidate" state.
            if len(stories) > 0 and blueprints_pushed == 0:
                trace_decision = "warning"
            elif errors:
                trace_decision = "warning"
            elif cross_niche_drops:
                # Cross-niche leak is niche.yaml contamination — always
                # surface at warning level even if blueprints_pushed > 0.
                trace_decision = "warning"
            else:
                trace_decision = "info"

            reasons_line = (
                f"stories={stories_pushed}, blueprints={blueprints_pushed}, "
                f"video_dedup_skipped={video_dedup_skipped}, "
                f"cross_niche_drops={cross_niche_drops}, "
                f"skip_llm_drops={skip_llm_drops}, errors={len(errors)}"
            )
            metadata = {
                "input_stories": len(stories),
                "stories_pushed": stories_pushed,
                "blueprints_pushed": blueprints_pushed,
                "video_dedup_skipped": video_dedup_skipped,
                "cross_niche_drops": cross_niche_drops,
                "skip_llm_drops": skip_llm_drops,
                "error_count": len(errors),
                # First 5 error strings so operators can see the
                # dominant failure without loading the full run report.
                "error_examples": errors[:5],
            }
            append_trace(
                context,
                stage="PushToBacklog",
                decision=trace_decision,
                confidence=1.0,
                reasons=[reasons_line],
                metadata=metadata,
            )
            record_decision(
                context,
                stage="PushToBacklog",
                decision=trace_decision,
                reason=reasons_line,
                confidence=1.0,
                metadata=metadata,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.warning(
                "[PushToBacklog] trace emission failed: %s", exc, exc_info=True
            )

        return context
