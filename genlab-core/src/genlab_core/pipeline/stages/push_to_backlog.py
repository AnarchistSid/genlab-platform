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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

from genlab_core.cache.stable_ids import generate_candidate_id, generate_story_id
from genlab_core.http.backlog_client import BacklogClient
from genlab_core.settings import settings
from genlab_core.utils.text_sanitizer import sanitize_for_graph_api

logger = logging.getLogger(__name__)


class PushToBacklog:
    """Push stories and blueprints to the shared SharePoint backlog.

    Niche-agnostic: ``niche_id`` is read from ``context["niche_id"]``
    at execution time. Raises ``ValueError`` if the key is missing.
    """

    def __init__(self) -> None:
        self._client: BacklogClient | None = None

    def _get_client(self, context: Dict[str, Any]) -> BacklogClient:
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

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:  # noqa: C901
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

        if not all([
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

        for story in stories:
            title = sanitize_for_graph_api(story.get("title", "Unknown"))
            source_url = story.get("source_url", "")
            published_at = story.get("published_at", datetime.now(timezone.utc).isoformat())
            story_id = generate_story_id(source_url, published_at)

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
                story_id, f"{niche_id}_default", content.get("hook", title),
            )
            story["candidate_id"] = candidate_id
            hook = content.get("hook", "")
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
                    client.blueprints.update(existing_bp[0]["id"], {"niche_id": niche_id})
                    logger.debug("[PUSH] Blueprint '%s' already exists", title)
                else:
                    story_record_id = story_record["id"]
                    fields: Dict[str, Any] = {
                        "candidate_id": candidate_id,
                        "story": [story_record_id],
                        "story_id": story_id,
                        "video_id": video_id,
                        "hook_text": hook,
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
                        "topic": story.get("source", niche_id),
                        "angle": (story.get("summary") or title)[:200],
                    }

                    if rendered_path:
                        fields["visual_paths"] = json.dumps([rendered_path])
                        # Auto-schedule for next 14:00 UTC publish window (19:30 IST)
                        # Pipelines run 08:30-10:30 UTC, content feeds tomorrow's window
                        tomorrow_utc = datetime.now(timezone.utc).date() + timedelta(days=1)
                        publish_time = datetime(
                            tomorrow_utc.year, tomorrow_utc.month, tomorrow_utc.day,
                            14, 0, tzinfo=timezone.utc,
                        )
                        fields["scheduled_for"] = publish_time.isoformat()
                    # clip_url and thumbnail_url intentionally omitted — not SharePoint columns

                    client.blueprints.create(fields, typecast=True)
                    blueprints_pushed += 1
                    logger.info(
                        "[PUSH] Created blueprint '%s' (status=%s)",
                        title, fields["status"],
                    )
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
