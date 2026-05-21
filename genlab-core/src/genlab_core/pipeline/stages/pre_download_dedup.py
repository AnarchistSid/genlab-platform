"""Pipeline stage: Pre-download dedup.

Drops stories whose source URL or video_id already has an active blueprint
in a blocking state (PUBLISHED/PUBLISHING/VISUAL_READY/DRAFTED/SCORED).
Runs BEFORE DownloadTopVideos so we don't waste 5-10 minutes rendering
content that will be deduped at push time anyway.

This stage reuses ``_BLOCKING_STATUSES`` and the same active-blueprint
query as ``PushToBacklog`` so the two stages agree on which content is
"already live". Archived/rejected rows don't block — they'll be revived
by the push stage if they reach it.

Reads:  context["stories"], context["niche_id"]
Writes: context["stories"] (filtered), context["run_stats"]["pre_download_dedup"]

Non-fatal: if the dedup query fails, all stories pass through and the
push stage still catches duplicates.
"""

from __future__ import annotations

import logging
from hashlib import sha256
from typing import Any

from genlab_core.http.backlog_client import BacklogClient
from genlab_core.pipeline.stages.push_to_backlog import _is_blocking

logger = logging.getLogger(__name__)


class PreDownloadDedup:
    """Filter stories against the active-blueprint dedup set before download."""

    def __init__(self) -> None:
        self._client: BacklogClient | None = None

    def _get_client(self) -> BacklogClient:
        if self._client is None:
            self._client = BacklogClient()
        return self._client

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        stories = context.get("stories", [])
        if not stories:
            return context
        niche_id = context.get("niche_id")
        if not niche_id:
            return context

        try:
            client = self._get_client()
        except Exception as exc:
            logger.warning(
                "[PreDownloadDedup] BacklogClient init failed — skipping: %s",
                exc,
            )
            return context

        try:
            recent_bps = client.blueprints.all(
                formula=f"{{niche_id}}='{niche_id}'",
                max_records=2000,
            )
        except Exception as exc:
            logger.warning(
                "[PreDownloadDedup] Blueprint load failed — skipping: %s",
                exc,
            )
            return context

        active_bps = [bp for bp in recent_bps if _is_blocking(bp)]
        seen_url_hashes: set[str] = set()
        seen_video_ids: set[str] = set()
        for bp in active_bps:
            fields = bp.get("fields", bp)
            url = (fields.get("video_url") or "").strip()
            if url:
                seen_url_hashes.add(sha256(url.encode()).hexdigest()[:16])
            vid = (fields.get("video_id") or "").strip()
            if vid:
                seen_video_ids.add(vid)

        kept: list[dict[str, Any]] = []
        dropped_url = dropped_vid = 0
        for story in stories:
            source_url = story.get("source_url", "") or ""
            video_id = story.get("video_id", "") or ""
            url_hash = sha256(source_url.encode()).hexdigest()[:16] if source_url else ""
            if url_hash and url_hash in seen_url_hashes:
                dropped_url += 1
                logger.info(
                    "[PreDownloadDedup] url-dedup: dropping '%s' "
                    "(URL already in active blueprint set)",
                    (story.get("title") or "")[:60],
                )
                continue
            if video_id and video_id in seen_video_ids:
                dropped_vid += 1
                logger.info(
                    "[PreDownloadDedup] video-dedup: dropping '%s' "
                    "(video_id=%s already in active blueprint set)",
                    (story.get("title") or "")[:60],
                    video_id[:16],
                )
                continue
            kept.append(story)

        context["stories"] = kept
        context.setdefault("run_stats", {})["pre_download_dedup"] = {
            "input_count": len(stories),
            "kept_count": len(kept),
            "dropped_url": dropped_url,
            "dropped_video_id": dropped_vid,
            "active_blueprint_urls": len(seen_url_hashes),
            "active_blueprint_video_ids": len(seen_video_ids),
        }
        logger.info(
            "[PreDownloadDedup] %d/%d kept (dropped %d url, %d video_id)",
            len(kept),
            len(stories),
            dropped_url,
            dropped_vid,
        )
        return context
