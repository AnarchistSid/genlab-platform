"""Convert raw SharePoint blueprint fields into a typed PublishPayload.

The publisher orchestrator (:mod:`genlab_core.publishing.publish_all_platforms`)
holds a dict of SharePoint field values per blueprint; each platform
client wants a typed :class:`~genlab_core.platforms.models.PublishPayload`.
:func:`build_payload` is the seam — one function per platform-bound
publish call. Three small helpers (``parse_visual_paths``,
``parse_json_field``, ``build_platform_specific``) own the data-shape
quirks (legacy JSON-string columns, per-platform sub-models).

Lives in its own module so the multi-platform publisher orchestrator
stays focused on orchestration flow. Extracted in the refactor-#9
decomposition (PR 3/N). Re-exported from the orchestrator for
backwards-compatible imports (``build_payload`` is used by
``test_publish_all_platforms``; the three helpers had zero external
references and dropped their underscore prefixes here since they are
public within this module).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from genlab_core.compliance.copyright_safety import (
    format_source_attribution,
    format_youtube_attribution,
)
from genlab_core.compliance.disclosure import apply_ai_disclosure
from genlab_core.platforms.models import (
    FacebookSpecific,
    InstagramSpecific,
    PublishPayload,
    ThreadsSpecific,
    TwitterSpecific,
    YouTubeSpecific,
)
from genlab_core.publishing.niche_validation import validate_niche
from genlab_core.publishing.transcode import transcode_for_platform

logger = logging.getLogger(__name__)


def parse_visual_paths(fields: dict[str, Any]) -> list:
    """Decode the ``visual_paths`` column into a list.

    Old rows store a JSON string; current rows can also pass a list
    pre-parsed. Malformed JSON returns ``[]`` — the caller treats
    missing visuals as a fatal condition further down so silent ``[]``
    is acceptable here.
    """
    raw = fields.get("visual_paths", "[]")
    try:
        return json.loads(raw) if isinstance(raw, str) else (raw or [])
    except (json.JSONDecodeError, TypeError):
        return []


def parse_json_field(raw: Any) -> dict:
    """Decode a JSON-string column into a dict.

    Used for legacy ``youtube_content`` (``{"title":...,"description":...}``)
    and the ``twitter_content`` thread spec. Empty or malformed values
    return ``{}`` so callers can fall through to per-platform defaults
    without an explicit error.
    """
    if not raw:
        return {}
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return {}


def build_platform_specific(
    fields: dict[str, Any],
    platform: str,
    caption: str,
    hook: str,
):
    """Build the platform-specific sub-model from blueprint fields.

    One branch per platform-client. Returns ``None`` for unknown
    platforms — the caller treats that as "no platform-specific
    config", which the strict clients (IG/YT) reject and the loose
    ones (FB/Threads) accept.
    """
    if platform == "instagram":
        image_paths = [
            str(p) for p in parse_visual_paths(fields) if not str(p).lower().endswith(".mp4")
        ]
        return InstagramSpecific(
            cover_url=image_paths[0] if image_paths else "",
            share_to_feed=True,
        )

    if platform == "youtube":
        # ``youtube_content`` is the plain-text Shorts description (after
        # affiliate CTA + disclosure injection by the CTA engine). Shorts
        # title comes from the ``hook`` column. Older rows may still
        # carry a legacy ``{"title":...,"description":...}`` JSON dict-
        # string — parse those defensively so historical blueprints
        # still publish.
        raw_yt = fields.get("youtube_content", "") or ""
        legacy = parse_json_field(raw_yt)
        if isinstance(legacy, dict) and legacy:
            description = legacy.get("description", "") or raw_yt
            legacy_title = legacy.get("title", "")
        else:
            description = raw_yt
            legacy_title = ""
        shorts_title = hook[:100] or legacy_title or fields.get("topic", "")

        # PR #568 (2026-06-25): copyright safety — append source
        # attribution (Footage: youtube.com/watch?v=…) to the
        # description so YouTube Content ID + the rights-holder
        # review staff see the fair-use intent. Idempotent
        # substring match prevents double-append on re-publish.
        # community_post_text falls back to caption when description
        # is empty — apply attribution to the chosen text so the
        # operator-supplied description ALSO gets attribution.
        body_text = description or caption
        attribution = format_youtube_attribution(fields)
        if attribution and attribution.strip() not in body_text:
            body_text = body_text + attribution

        return YouTubeSpecific(
            shorts_title=shorts_title[:100],
            community_post_text=body_text,
        )

    if platform == "twitter":
        tw_content = parse_json_field(fields.get("twitter_content", ""))
        routing = (
            str(tw_content.get("routing", tw_content.get("strategy", "single"))).strip().lower()
        )
        tweet_text = str(tw_content.get("tweet_text", "") or caption).strip()
        # PR #567 (2026-06-25): AI disclosure with 280-char budget.
        # Must run BEFORE the [:280] truncation — apply_ai_disclosure
        # itself handles the budget by truncating the original caption
        # to make room for the disclosure suffix. Without this order,
        # the [:280] would cut off the disclosure half-way.
        tweet_text = apply_ai_disclosure(
            tweet_text,
            "twitter",
            niche_id=fields.get("niche_id", "") or "",
            blueprint_id=fields.get("id"),
        )
        tweet_text = tweet_text[:280]
        return TwitterSpecific(
            routing=routing if routing in ("single", "thread") else "single",
            tweet_text=tweet_text,
            thread_tweets=tw_content.get("thread_tweets", []),
        )

    if platform == "facebook":
        return FacebookSpecific()

    if platform == "threads":
        return ThreadsSpecific()

    return None


def build_payload(fields: dict[str, Any], platform: str) -> PublishPayload:
    """Convert a raw blueprint ``fields`` dict into a typed PublishPayload.

    Args:
        fields: The ``fields`` dict from a SharePoint blueprint record.
        platform: Legacy platform name (e.g. ``instagram``, ``twitter``).

    Raises:
        ValueError: If ``media_type`` is video but no valid media files
            remain after filtering — publish would silent-fail otherwise.
    """
    niche_id = validate_niche(fields.get("niche_id", "") or "")

    # Media paths — decode + filter for existence + min size.
    visual_paths_raw = fields.get("visual_paths", "[]")
    try:
        vp_list = (
            json.loads(visual_paths_raw)
            if isinstance(visual_paths_raw, str)
            else (visual_paths_raw or [])
        )
    except (json.JSONDecodeError, TypeError):
        vp_list = []
    media_paths = [Path(p) for p in vp_list if p]

    missing = [p for p in media_paths if not p.exists()]
    if missing:
        logger.warning("[publish] Missing media files: %s", [str(m) for m in missing])
        media_paths = [p for p in media_paths if p.exists()]

    # Filter out corrupted/empty files (< 10KB is not a valid video).
    too_small = [p for p in media_paths if p.exists() and p.stat().st_size < 10240]
    if too_small:
        logger.warning(
            "[publish] Skipping too-small media files (<10KB): %s", [str(p) for p in too_small]
        )
        media_paths = [p for p in media_paths if p not in too_small]

    # Per-platform transcode: produce an optimised variant for the target.
    if media_paths and any(str(p).lower().endswith(".mp4") for p in media_paths):
        media_paths = [
            transcode_for_platform(p, platform) if str(p).lower().endswith(".mp4") else p
            for p in media_paths
        ]

    fmt = (fields.get("format", "") or "").strip().lower()
    has_mp4 = any(str(p).lower().endswith(".mp4") for p in media_paths)
    if has_mp4 or fmt in ("reel", "short", "video"):
        media_type = "video"
    else:
        media_type = "image"

    # Abort early if video format but no valid media files remain — without
    # this the publisher would post nothing and report success.
    if media_type == "video" and not media_paths:
        raise ValueError(
            f"No valid media files for video publish (niche={niche_id}, "
            f"format={fmt}, original_paths={vp_list})"
        )

    # Caption — use platform-specific content when available.
    if platform == "threads" and fields.get("threads_content"):
        caption = (fields.get("threads_content", "") or "").strip()
    elif platform == "facebook" and fields.get("facebook_content"):
        caption = (fields.get("facebook_content", "") or "").strip()
    else:
        caption = (fields.get("caption", "") or "").strip()

    # PR #567 (2026-06-25): apply per-platform AI disclosure
    # (YouTube/Meta/TikTok 2024 AI-content policies). Idempotent —
    # re-publishing won't double-append. Budget-aware — for platforms
    # with hard char caps (twitter handled separately in
    # build_platform_specific where the 280-cap lives). Logs to
    # compliance_events on every successful append.
    #
    # Apply AFTER caption selection (so platform-override captions
    # like threads_content / facebook_content also get disclosure)
    # but BEFORE build_platform_specific (so the platform-specific
    # sub-models receive the disclosure-augmented caption when they
    # fall back to it). Twitter is the exception — its tweet_text
    # branch in build_platform_specific applies its own disclosure
    # with the 280-char budget aware.
    caption = apply_ai_disclosure(
        caption,
        platform,
        niche_id=niche_id,
        blueprint_id=fields.get("id"),
    )

    # PR #Layer-Publisher (2026-07-10, Markanimation incident):
    # publish-time source-creator attribution backstop for FB / IG /
    # Threads. YouTube already has ``format_youtube_attribution`` wired
    # into ``build_platform_specific`` below; this block extends the
    # same guarantee to Meta platforms so credit lands even when the
    # writer-side ``content["source_attribution"]`` wasn't populated
    # (LLM refusal, empty channel_name at write time, pre-fix legacy
    # blueprints from the 90-day retroactive audit window).
    #
    # Twitter is excluded — its 280-char budget is enforced separately
    # in the platform-specific branch and can't absorb the credit line
    # without truncating the take itself.
    #
    # Idempotent via substring guard so re-publish doesn't double-
    # append. Reads ``source_channel_title`` from top-level fields
    # OR from ``extra`` JSONB (Postgres SplitPromoted pattern).
    if platform in ("facebook", "instagram", "threads"):
        _extra_container = fields.get("extra") or {}
        if not isinstance(_extra_container, dict):
            _extra_container = {}
        _src_attr = format_source_attribution(
            {
                "video_id": fields.get("video_id"),
                "source": fields.get("source") or "youtube_trending",
                "source_channel_title": (
                    fields.get("source_channel_title")
                    or _extra_container.get("source_channel_title")
                    or fields.get("channel_name")
                ),
            }
        )
        # 2026-08-12 (F-QB-0708): soften idempotence guard from
        # exact-string match to marker match. Prior behavior compared
        # the full `_src_attr` ("🎬 Original: @X — https://URL") against
        # the LLM's output — but the LLM often wrote a partial variant
        # like "🎬 Original: @X — " (no URL) or "🎬 Original creator:
        # @X". Exact match missed → guard didn't fire → BOTH forms
        # coexisted in the caption. Result: 36% of recent captions had
        # double "🎬 Original:" markers, matching YouTube's inauthentic-
        # content template signature (F-QB-0708). Marker-only check
        # catches every variant the LLM produces.
        if (
            _src_attr
            and _src_attr.strip()
            and "🎬 Original:" not in caption
            and "🎬 Original creator:" not in caption
        ):
            caption = caption.rstrip() + "\n\n" + _src_attr

    # Hashtags — accept either a list or a space-separated string.
    hashtags_raw = fields.get("hashtags", "") or ""
    if isinstance(hashtags_raw, list):
        hashtags = [
            t.strip() if t.strip().startswith("#") else f"#{t.strip()}"
            for t in (str(h) for h in hashtags_raw if h)
            if t.strip()
        ]
    else:
        hashtags = [
            t.strip() if t.strip().startswith("#") else f"#{t.strip()}"
            for t in str(hashtags_raw).split()
            if t.strip()
        ]
    hook = (fields.get("hook", "") or fields.get("hook_text", "") or "").strip()

    platform_specific = build_platform_specific(fields, platform, caption, hook)

    # Per-platform follow-up comment text. Affiliate URLs ship as a
    # comment/reply after the main post — keeps captions clean and avoids
    # algorithmic downranking on Facebook and X/Twitter.
    #
    # 2026-07-17 (Layer 2 monetization): added IG + YT. Empirical
    # bio-link CTR is 0.1-0.3%; pinned first-comment CTR is 2-8%.
    # Adding IG+YT to the dispatch is the highest-leverage single
    # monetization change per audit round 4 — 20-80× CTR jump with
    # ZERO extra traffic. Reads `{platform}_first_comment` field
    # populated by cta_engine.py's expanded per-platform CTA generation.
    first_comment_text = ""
    if platform == "facebook":
        first_comment_text = (fields.get("facebook_first_comment", "") or "").strip()
    elif platform in ("twitter", "x_twitter"):
        # R-46: the default platform list uses the legacy name "twitter"; this
        # branch previously only matched "x_twitter", silently dropping the X
        # affiliate self-reply (the entire X monetization payload).
        first_comment_text = (fields.get("twitter_first_comment", "") or "").strip()
    elif platform == "instagram":
        first_comment_text = (fields.get("instagram_first_comment", "") or "").strip()
    elif platform == "youtube":
        first_comment_text = (fields.get("youtube_first_comment", "") or "").strip()
    elif platform == "threads":
        # 2026-07-22 (Layer 2 monetization): threads_first_comment wire.
        # Threads reply payload; posted after successful parent publish
        # by ThreadsClient.publish's post_reply call. Same CTR-boost
        # pattern as FB/IG/YT. Populated by cta_engine.py:504-518.
        first_comment_text = (fields.get("threads_first_comment", "") or "").strip()

    return PublishPayload(
        caption=caption,
        media_paths=media_paths,
        media_type=media_type,
        hashtags=hashtags,
        hook=hook,
        niche_id=niche_id,
        platform_specific=platform_specific,
        first_comment_text=first_comment_text,
    )
