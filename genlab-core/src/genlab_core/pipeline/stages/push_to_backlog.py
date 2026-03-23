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
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from genlab_core.cache.stable_ids import generate_candidate_id, generate_story_id
from genlab_core.http.backlog_client import BacklogClient
from genlab_core.settings import settings
from genlab_core.utils.text_sanitizer import sanitize_for_graph_api

logger = logging.getLogger(__name__)

# Default arm mapping per niche — maps content signals to bandit arm IDs.
# These must match the arm_id values in the bandit_arms table.
_NICHE_ARM_DEFAULTS: dict[str, str] = {
    "gaming": "gameplay_clip",
    "sports": "highlight_play",
    "movies": "trailer_drop",
    "anime": "episode_moment",
    "ai_creators": "tool_demo",
}

_ARM_KEYWORDS: dict[str, list[tuple[str, list[str]]]] = {
    "gaming": [
        ("esports_highlight", ["esports", "tournament", "championship", "league", "competitive"]),
        ("trailer_reaction", ["trailer", "reveal", "announcement", "launch", "release"]),
        ("patch_news", ["patch", "update", "nerf", "buff", "season", "hotfix"]),
    ],
    "sports": [
        ("breaking_trade", ["trade", "transfer", "sign", "contract", "deal", "free agent"]),
        ("record_milestone", ["record", "milestone", "history", "first ever", "youngest", "oldest"]),
        ("upset_reaction", ["upset", "shock", "underdog", "eliminated", "comeback"]),
    ],
    "movies": [
        ("scene_reaction", ["scene", "moment", "clip", "reaction", "watch"]),
        ("box_office_update", ["box office", "opening weekend", "gross", "million", "billion"]),
        ("cast_reveal", ["cast", "role", "playing", "starring", "joins", "reveal"]),
    ],
    "anime": [
        ("season_announcement", ["season", "announced", "confirmed", "adaptation", "premiere"]),
        ("fight_scene", ["fight", "battle", "vs", "clash", "power", "epic"]),
        ("adaptation_news", ["manga", "light novel", "adaptation", "studio", "animated"]),
    ],
    "ai_creators": [
        ("model_release", ["release", "launched", "model", "gpt", "claude", "gemini", "llm"]),
        ("creative_showcase", ["created", "made", "generated", "art", "music", "video", "film"]),
        ("comparison_test", ["vs", "compared", "better", "benchmark", "test", "which"]),
    ],
}


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


def _get_arm_boost(client, niche_id: str) -> dict[str, float]:
    """Query recent engagement data and return boost multipliers per arm_id.

    Arms with above-average engagement get a 1.1-1.3x boost.
    Arms with below-average get 0.8-0.9x.
    Unknown arms get 1.0 (neutral).
    """
    try:
        # Get recently published blueprints with engagement data
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

        # Calculate average score per arm
        arm_avgs = {arm: sum(scores) / len(scores) for arm, scores in arm_scores.items()}
        overall_avg = sum(arm_avgs.values()) / len(arm_avgs) if arm_avgs else 0.5

        # Generate boost multipliers
        boosts = {}
        for arm, avg in arm_avgs.items():
            if overall_avg > 0:
                ratio = avg / overall_avg
                boosts[arm] = max(0.8, min(1.3, ratio))  # clamp to 0.8-1.3
            else:
                boosts[arm] = 1.0

        return boosts
    except Exception:
        return {}


def _apply_engagement_boost(base_score: float, arm_id: str, boosts: dict[str, float]) -> float:
    """Apply engagement-based boost to priority score."""
    multiplier = boosts.get(arm_id, 1.0)
    return round(base_score * multiplier, 4)


def _classify_arm(niche_id: str, story: dict, content: dict) -> str:
    """Classify content into a bandit arm_id based on keyword matching."""
    text = f"{story.get('title', '')} {content.get('hook', '')} {story.get('summary', '')}".lower()

    for arm_id, keywords in _ARM_KEYWORDS.get(niche_id, []):
        if any(kw in text for kw in keywords):
            return arm_id

    return _NICHE_ARM_DEFAULTS.get(niche_id, "default")


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

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:  # noqa: C901
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

        import os
        _use_postgres = os.getenv("GENLAB_USE_POSTGRES", "").lower() == "true"
        if not _use_postgres and not all([
            settings.azure_tenant_id,
            settings.azure_client_id,
            settings.azure_client_secret,
            settings.sharepoint_site_id,
        ]):
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

        # Load recent hooks for this niche to prevent cross-run duplicates
        existing_hooks: set[str] = set()
        try:
            recent_bps = client.blueprints.all(
                formula=f"{{niche_id}}='{niche_id}'",
                max_records=500,
            )
            for bp in recent_bps:
                h = (bp.get("fields", bp).get("hook") or "").strip().lower()
                if h:
                    existing_hooks.add(h)
            if existing_hooks:
                logger.info("[PUSH] Loaded %d existing hooks for dedup", len(existing_hooks))
        except Exception as e:
            logger.debug("[PUSH] Could not load existing hooks: %s", e)
        context["existing_hooks"] = existing_hooks

        # Load engagement-based arm boost multipliers
        arm_boosts = _get_arm_boost(client, niche_id)
        if arm_boosts:
            logger.info("[PUSH] Loaded engagement boosts for %d arms: %s",
                        len(arm_boosts), {k: f"{v:.2f}" for k, v in arm_boosts.items()})

        # Load content_memory hashes + existing story URLs for cross-run dedup
        seen_urls: set[str] = set()
        try:
            # Load URL hashes from existing stories (catches recurring sources)
            from hashlib import sha256 as _sha256
            existing_stories = client.stories.all(
                formula=f"{{niche_id}}='{niche_id}'",
                max_records=500,
            )
            for s in existing_stories:
                url = (s.get("fields", s).get("url") or "").strip()
                if url:
                    seen_urls.add(_sha256(url.encode()).hexdigest()[:16])
            if existing_stories:
                logger.info("[PUSH] Loaded %d existing story URL hashes for cross-run dedup", len(seen_urls))
        except Exception as e:
            logger.debug("[PUSH] Could not load existing story URLs: %s", e)

        try:
            cm_proxy = getattr(client, "content_memory", None)
            if cm_proxy:
                cm_records = cm_proxy.all(
                    formula=f"{{niche_id}}='{niche_id}'",
                    max_records=500,
                )
                for rec in cm_records:
                    h = (rec.get("fields", rec).get("content_hash") or "").strip()
                    if h:
                        seen_urls.add(h)
                logger.info("[PUSH] Total dedup hashes: %d", len(seen_urls))
        except Exception as e:
            logger.debug("[PUSH] content_memory load failed (non-fatal): %s", e)

        for story in stories:
            title = sanitize_for_graph_api(story.get("title", "Unknown"))
            source_url = story.get("source_url", "")
            published_at = story.get("published_at", datetime.now(UTC).isoformat())

            # URL-only dedup FIRST — catches recurring sources (AniList, Jikan)
            # that return the same URL with different timestamps each fetch.
            from hashlib import sha256
            url_hash = sha256(source_url.encode()).hexdigest()[:16] if source_url else ""
            if url_hash and url_hash in seen_urls:
                logger.debug("[PUSH] URL already seen (url_hash dedup): %s", title[:60])
                continue

            story_id = generate_story_id(source_url, published_at)

            # Also check story_id-based dedup (backward compat)
            if story_id in seen_urls:
                logger.debug("[PUSH] URL already in content_memory: %s", title[:60])
                continue

            # Track URL hash so future stories with same URL are skipped
            if url_hash:
                seen_urls.add(url_hash)

            # Upsert story
            try:
                existing = client.find_story_by_story_id(story_id)
                if existing:
                    client.stories.update(existing["id"], {"niche_id": niche_id})
                    story_record = existing
                    logger.debug("[PUSH] Story '%s' already exists, updated niche_id", title)
                else:
                    record = client.stories.create({
                        "story_id": story_id,
                        "title": title,
                        "url": source_url,
                        "source": story.get("source", niche_id),
                        "published_at": published_at,
                        "summary": (story.get("summary") or "")[:255],
                        "priority": (
                            story.get("final_score") if story.get("final_score") is not None
                            else story.get("composite_score") if story.get("composite_score") is not None
                            else story.get("score", 0.5)
                        ),
                        "status": "INTAKE",
                        "niche_id": niche_id,
                    })
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

            # Freshness gate: skip stories older than 7 days
            story_published = story.get("published_at", "")
            if story_published:
                try:
                    pub_dt = datetime.fromisoformat(story_published)
                    if pub_dt.tzinfo is None:
                        pub_dt = pub_dt.replace(tzinfo=UTC)
                    age_days = (datetime.now(UTC) - pub_dt).days
                    if age_days > 7:
                        logger.debug("[PUSH] Story too old (%d days): %s", age_days, title[:40])
                        continue
                except (ValueError, TypeError):
                    pass  # unparseable date — allow through

            # Create blueprint from content
            content = story.get("content", {})
            if not content:
                continue
            # Content may be a JSON string from the writing stage
            if isinstance(content, str):
                try:
                    content = json.loads(content)
                except (json.JSONDecodeError, TypeError):
                    logger.warning("[PUSH] Blueprint '%s' has unparseable content string", title)
                    continue

            # Extract video_id for dedup — available from FetchTrendingVideos or DownloadTopVideos
            video_id = story.get("video_id", "")
            if not video_id:
                # Try clip_index lookup
                clip_index = context.get("clip_index", {})
                clip_entry = clip_index.get("clips", {}).get(story_id, {})
                source_url_for_vid = clip_entry.get("source_url", "")
                if "youtube" in source_url_for_vid:
                    video_id = source_url_for_vid.split("v=")[-1].split("&")[0]

            # Video-level dedup: same clip must not create multiple blueprints
            if video_id:
                try:
                    existing_by_video = client.blueprints.all(
                        formula=f"{{video_id}}='{video_id}'",
                        niche_id=niche_id,
                        max_records=1,
                    )
                    if existing_by_video:
                        logger.info(
                            "[PUSH] Video already blueprinted: video_id=%s niche=%s — skipping",
                            video_id[:20], niche_id,
                        )
                        video_dedup_skipped += 1
                        continue
                except Exception as e:
                    logger.warning("[PUSH] Video dedup check failed: %s — allowing through", e)

            candidate_id = generate_candidate_id(
                story_id, niche_id, video_id or title,
            )
            story["candidate_id"] = candidate_id
            hook = content.get("hook", "")

            # Classify content into a bandit arm_id for the learning loop
            arm_id = _classify_arm(niche_id, story, content)

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
                                100 * intersection / union, hook[:40], existing[:40],
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

            rendered_path = (story.get("media") or {}).get("rendered_path", "")

            try:
                existing_bp = client.blueprints.all(
                    formula=f"{{candidate_id}}='{candidate_id}'",
                    max_records=1,
                )
                if existing_bp:
                    existing_status = (
                        existing_bp[0].get("fields", existing_bp[0]).get("status", "")
                    )
                    if existing_status in ("PUBLISHED", "PUBLISHING", "VISUAL_READY"):
                        logger.info(
                            "[PUSH] Blueprint '%s' already %s — skipping",
                            title, existing_status,
                        )
                    else:
                        # DRAFTED or SCORED — safe to update
                        client.blueprints.update(existing_bp[0]["id"], {"niche_id": niche_id})
                        logger.debug("[PUSH] Blueprint '%s' already exists (%s), updated niche_id", title, existing_status)
                else:
                    story_record_id = story_record["id"]
                    fields: dict[str, Any] = {
                        "candidate_id": candidate_id,
                        "story": [story_record_id],
                        "story_id": story_id,
                        "video_id": video_id,
                        "video_url": story.get("source_url", ""),
                        "hook": hook,
                        "hook_text": hook,
                        "title": title,
                        "caption": ig.get("caption", ""),
                        "hashtags": " ".join(ig.get("hashtags", []) or re.findall(r"#\w+", ig.get("caption", ""))),
                        "youtube_content": json.dumps({"title": yt.get("title", ""), "description": yt.get("description", "")}),
                        "twitter_content": json.dumps({"tweet_text": tw.get("tweet", tw.get("tweet_text", "")), "routing": tw.get("routing", "single")}),
                        "facebook_content": fb.get("caption", ""),
                        "priority_score": _apply_engagement_boost(
                            story.get("final_score") if story.get("final_score") is not None
                            else story.get("composite_score") if story.get("composite_score") is not None
                            else story.get("score", 0.5),
                            arm_id, arm_boosts,
                        ),
                        "status": "VISUAL_READY" if rendered_path else "DRAFTED",
                        "format": "reel",
                        "niche_id": niche_id,
                        "arm_id": arm_id,
                        "topic": _normalize_topic(story.get("source", niche_id)),
                        "angle": (story.get("summary") or title)[:200],
                    }

                    # Affiliate fields (if matched by AffiliateMatch stage)
                    for af_key in (
                        "affiliate_product", "affiliate_url", "affiliate_network",
                        "affiliate_commission_pct", "affiliate_cta",
                    ):
                        if story.get(af_key):
                            fields[af_key] = story[af_key]

                    # Inject platform-specific CTAs into captions
                    if story.get("affiliate_product"):
                        from genlab_core.monetization.cta_engine import inject_cta
                        fields = inject_cta(fields, story)

                    if rendered_path:
                        fields["visual_paths"] = json.dumps([rendered_path])
                        # Auto-schedule for next available 06:30 UTC = 12:00 IST
                        # publish window.  Use today's slot if it hasn't passed yet,
                        # otherwise fall back to tomorrow.
                        now_utc = datetime.now(UTC)
                        today_slot = datetime(
                            now_utc.year, now_utc.month, now_utc.day,
                            6, 30, tzinfo=UTC,
                        )
                        if today_slot > now_utc:
                            publish_time = today_slot
                        else:
                            next_day = now_utc.date() + timedelta(days=1)
                            publish_time = datetime(
                                next_day.year, next_day.month, next_day.day,
                                6, 30, tzinfo=UTC,
                            )
                        fields["scheduled_for"] = publish_time.isoformat()
                    # clip_url and thumbnail_url intentionally omitted — not SharePoint columns

                    client.blueprints.create(fields, typecast=True)
                    blueprints_pushed += 1
                    logger.info(
                        "[PUSH] Created blueprint '%s' (status=%s)",
                        title, fields["status"],
                    )
                    # Record in content_memory for persistent URL-level dedup
                    try:
                        cm = getattr(client, "content_memory", None)
                        if cm and story_id not in seen_urls:
                            cm.create({
                                "content_hash": story_id,
                                "title": title[:200],
                                "url": source_url,
                                "niche_id": niche_id,
                                "first_seen": datetime.now(UTC).isoformat(),
                                "last_seen": datetime.now(UTC).isoformat(),
                            })
                            seen_urls.add(story_id)
                    except Exception:
                        pass  # non-critical — other dedup layers cover it
            except Exception as e:
                logger.warning("[PUSH] Blueprint '%s' failed: %s", title, e)
                errors.append(f"blueprint:{title}: {e}")

        context.setdefault("run_stats", {})["backlog_push"] = {
            "stories_pushed": stories_pushed,
            "blueprints_pushed": blueprints_pushed,
            "video_dedup_skipped": video_dedup_skipped,
            "errors": errors[:5],
            "status": "ok" if not errors else f"partial ({len(errors)} errors)",
        }

        logger.info(
            "[PUSH] %d stories, %d blueprints pushed to backlog (%d video-dedup skipped, %d errors)",
            stories_pushed, blueprints_pushed, video_dedup_skipped, len(errors),
        )
        return context
