"""Pipeline stage: Enforce platform rules with gaming-specific overrides.

Runs AFTER WriteGamingContent (content exists) and BEFORE rendering/publish.
Applies all 6 platform rules that were previously missing:

  1. Instagram: Strip external URLs from caption
  2. Instagram: Enforce 3–5 hashtags (pad from pool / trim excess)
  3. Instagram: Append gaming CTA at caption end (deterministic selection)
  4. X/Twitter: Move links to first_reply (never in main tweet)
  5. X/Twitter: Set safe_zone_enforced flag for 9:16 video
  6. Universal: Convert US date format to international

The two existing rules (YouTube question title, Facebook engagement) are
handled by WriteGamingContent and are NOT touched here.

Usage:
    stage = AdaptGamingContent()
    context = stage.execute(context)
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any

import yaml
from genlab_core.platforms.rules import enforce_platform_rules
from genlab_core.strategies import PlatformAdaptationStrategy

logger = logging.getLogger(__name__)

NICHE_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = NICHE_ROOT.parent.parent

_overrides_cache: dict[str, Any] | None = None
_templates_cache: dict[str, Any] | None = None


def _load_overrides() -> dict[str, Any]:
    """Load config/platform_overrides.yaml (cached)."""
    global _overrides_cache
    if _overrides_cache is not None:
        return _overrides_cache
    path = PROJECT_ROOT / "config" / "platform_overrides.yaml"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            _overrides_cache = yaml.safe_load(f) or {}
    else:
        _overrides_cache = {}
    return _overrides_cache


def _load_templates() -> dict[str, Any]:
    """Load niches/gaming/config/templates.yaml (cached)."""
    global _templates_cache
    if _templates_cache is not None:
        return _templates_cache
    path = NICHE_ROOT / "config" / "templates.yaml"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            _templates_cache = yaml.safe_load(f) or {}
    else:
        _templates_cache = {}
    return _templates_cache


def _select_cta(story_key: str, cta_phrases: list[str]) -> str:
    """Deterministic CTA selection based on story key hash."""
    digest = hashlib.sha256(story_key.encode()).hexdigest()
    idx = int(digest, 16) % len(cta_phrases)
    return cta_phrases[idx]


def _enforce_hashtag_count(
    caption: str,
    hashtags: list[str],
    hashtag_pool: list[str],
    min_count: int,
    max_count: int,
) -> tuple[str, list[str]]:
    """Pad or trim hashtags to stay within [min_count, max_count].

    Returns updated (caption, hashtags).
    """
    count = len(hashtags)

    if count < min_count and hashtag_pool:
        existing_lower = {h.lower() for h in hashtags}
        candidates = [h for h in hashtag_pool if h.lower() not in existing_lower]
        needed = min_count - count
        to_add = candidates[:needed]
        hashtags = hashtags + to_add
        logger.warning(
            "[ADAPT] Instagram: padded hashtags from %d to %d (added %s)",
            count,
            len(hashtags),
            " ".join(to_add),
        )
    elif count > max_count:
        trimmed = hashtags[max_count:]
        hashtags = hashtags[:max_count]
        logger.warning(
            "[ADAPT] Instagram: trimmed hashtags from %d to %d (removed %s)",
            count,
            max_count,
            " ".join(trimmed),
        )

    return caption, hashtags


def adapt_story_content(
    content: dict[str, Any],
    story: dict[str, Any],
    overrides: dict[str, Any],
    templates: dict[str, Any],
) -> dict[str, Any]:
    """Apply platform rules to a single story's content dict.

    Mutates content in place and returns it. Each rule is idempotent.
    """
    source_url = story.get("source_url", "")
    story_key = source_url or story.get("title", "unknown")
    ig_overrides = overrides.get("instagram", {})
    x_overrides = overrides.get("x_twitter", {})
    captions_cfg = templates.get("captions", {})
    hashtag_pool = captions_cfg.get("hashtag_pool", [])

    # --- Instagram ---
    ig = content.get("instagram", {})
    if ig.get("caption"):
        cta_phrases = ig_overrides.get("cta_phrases", [])
        cta = _select_cta(story_key, cta_phrases) if cta_phrases else None

        adapted = enforce_platform_rules(
            platform="instagram",
            title=content.get("hook", ""),
            caption=ig["caption"],
            hashtags=ig.get("hashtags", []),
            url=source_url,
            cta=cta,
        )
        ig["caption"] = adapted.caption
        if adapted.hashtags:
            ig["hashtags"] = adapted.hashtags

        # Hashtag count enforcement (pad / trim)
        min_h = ig_overrides.get("hashtag_min", 3)
        max_h = ig_overrides.get("hashtag_max", 5)
        ig["caption"], ig["hashtags"] = _enforce_hashtag_count(
            ig["caption"],
            ig.get("hashtags", []),
            hashtag_pool,
            min_h,
            max_h,
        )

    # --- X/Twitter ---
    tw = content.get("x_twitter", {})
    if tw.get("tweet"):
        adapted = enforce_platform_rules(
            platform="twitter",
            title="",
            caption=tw["tweet"],
            url=source_url,
        )
        tw["tweet"] = adapted.caption

        # Format first_reply with gaming template
        link_template = x_overrides.get("link_reply_template", "Source: {url}")
        if adapted.first_comment:
            tw["first_reply"] = link_template.format(url=adapted.first_comment)
        elif source_url:
            tw["first_reply"] = link_template.format(url=source_url)

        # Safe-zone crop flag for 9:16 video
        media = story.get("media") or {}
        aspect = media.get("aspect_ratio", "")
        rendered_path = media.get("rendered_path")
        if rendered_path and aspect in ("9:16", "9/16", "vertical"):
            if not tw.get("safe_zone_enforced"):
                tw["safe_zone_enforced"] = True
                tw["safe_zone_padding_pct"] = x_overrides.get("safe_zone_padding_pct", 0.08)
                logger.info("[ADAPT] X/Twitter: set safe_zone_enforced for 9:16 video")

    # --- Facebook ---
    fb = content.get("facebook", {})
    if fb.get("caption"):
        fb_overrides = overrides.get("facebook", {})
        max_caption = fb_overrides.get("max_caption_length", 2200)

        adapted = enforce_platform_rules(
            platform="facebook",
            title="",
            caption=fb["caption"],
        )
        fb["caption"] = adapted.caption

        # Truncate to platform limit
        if len(fb["caption"]) > max_caption:
            fb["caption"] = fb["caption"][: max_caption - 1] + "…"
            logger.warning(
                "[ADAPT] Facebook: caption truncated to %d chars",
                max_caption,
            )

    # --- TikTok ---
    tk = content.get("tiktok", {})
    if tk.get("caption"):
        tk_overrides = overrides.get("tiktok", {})
        max_caption = tk_overrides.get("max_caption_length", 2200)

        # Strip external URLs — they render as unclickable text on TikTok
        tk["caption"] = re.sub(r"https?://\S+", "", tk["caption"]).strip()

        # Ensure hook is in first 150 chars (title display limit)
        hook = content.get("hook", "")
        if hook and not tk["caption"].startswith(hook):
            tk["caption"] = f"{hook}\n\n{tk['caption']}"

        # Enforce hashtag count (3-5, mix niche + trending)
        tk_hashtags = tk.get("hashtags", [])
        tk_pool = hashtag_pool + ["#fyp", "#foryou", "#foryoupage"]
        _, tk["hashtags"] = _enforce_hashtag_count(
            tk["caption"],
            tk_hashtags,
            tk_pool,
            3,
            5,
        )

        # Truncate
        if len(tk["caption"]) > max_caption:
            tk["caption"] = tk["caption"][: max_caption - 1] + "…"
            logger.warning("[ADAPT] TikTok: caption truncated to %d chars", max_caption)

    # --- Threads ---
    th = content.get("threads", {})
    if th.get("caption"):
        th_overrides = overrides.get("threads", {})
        max_caption = th_overrides.get("max_caption_length", 500)

        adapted = enforce_platform_rules(
            platform="threads",
            title="",
            caption=th["caption"],
        )
        th["caption"] = adapted.caption

        # Threads is text-first — ensure caption is conversational
        # Truncate to 500 chars
        if len(th["caption"]) > max_caption:
            th["caption"] = th["caption"][: max_caption - 1] + "…"
            logger.warning("[ADAPT] Threads: caption truncated to %d chars", max_caption)

    return content


class AdaptGamingContent(PlatformAdaptationStrategy):
    """Enforce platform rules with gaming-specific overrides."""

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        stories = context.get("stories", [])
        overrides = _load_overrides()
        templates = _load_templates()

        stats: dict[str, Any] = {
            "adapted": 0,
            "skipped_no_content": 0,
            "warnings": 0,
        }

        for story in stories:
            content = story.get("content")
            if not content:
                stats["skipped_no_content"] += 1
                continue

            adapt_story_content(content, story, overrides, templates)
            stats["adapted"] += 1

        context.setdefault("run_stats", {})["adapt"] = stats
        logger.info(
            "[ADAPT] %d stories adapted, %d skipped (no content)",
            stats["adapted"],
            stats["skipped_no_content"],
        )
        return context
