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
                max_records=50,
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

        # Load content_memory hashes for URL-level dedup
        seen_urls: set[str] = set()
        try:
            cm_proxy = getattr(client, "content_memory", None)
            if cm_proxy:
                cm_records = cm_proxy.all(
                    formula=f"{{niche_id}}='{niche_id}'",
                    max_records=200,
                )
                for rec in cm_records:
                    h = (rec.get("fields", rec).get("content_hash") or "").strip()
                    if h:
                        seen_urls.add(h)
                if seen_urls:
                    logger.info("[PUSH] Loaded %d content_memory hashes for URL dedup", len(seen_urls))
        except Exception as e:
            logger.debug("[PUSH] content_memory load failed (non-fatal): %s", e)

        for story in stories:
            title = sanitize_for_graph_api(story.get("title", "Unknown"))
            source_url = story.get("source_url", "")
            published_at = story.get("published_at", datetime.now(UTC).isoformat())
            story_id = generate_story_id(source_url, published_at)

            # URL-level dedup via content_memory
            if story_id in seen_urls:
                logger.debug("[PUSH] URL already in content_memory: %s", title[:60])
                continue

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
                    existing_by_video = [
                        bp for bp in client.blueprints.all(max_records=200)
                        if (bp.get("fields", bp).get("video_id", "") == video_id
                            and bp.get("fields", bp).get("niche_id", "") == niche_id)
                    ]
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

            # Cross-run hook dedup: reject exact duplicate hooks
            if hook and hook.strip().lower() in existing_hooks:
                logger.info(
                    "[PUSH] Hook already used in niche %s, skipping: '%s'",
                    niche_id, hook[:60],
                )
                continue
            if hook:
                existing_hooks.add(hook.strip().lower())
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
                        "youtube_content": f"{yt.get('title', '')}\n\n{yt.get('description', '')}",
                        "twitter_content": tw.get("tweet", ""),
                        "facebook_content": fb.get("caption", ""),
                        "priority_score": (
                            story.get("final_score") if story.get("final_score") is not None
                            else story.get("composite_score") if story.get("composite_score") is not None
                            else story.get("score", 0.5)
                        ),
                        "status": "VISUAL_READY" if rendered_path else "DRAFTED",
                        "format": "reel",
                        "niche_id": niche_id,
                        "arm_id": arm_id,
                        "topic": story.get("source", niche_id),
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
