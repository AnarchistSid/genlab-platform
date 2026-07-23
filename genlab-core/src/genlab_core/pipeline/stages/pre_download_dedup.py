"""Pipeline stage: Pre-download dedup.

Drops stories whose source URL or video_id already has an *active live* (or
about-to-be-live) blueprint — i.e. PUBLISHED / PUBLISHING / VISUAL_READY.
Runs BEFORE DownloadTopVideos so we don't waste 5-10 minutes downloading
content that's already shipped.

DRAFTED and SCORED are deliberately NOT blocking here: those statuses mean
"render failed, retry next run" or "scored but not yet drafted". Including
them in the blocking set (as PushToBacklog does, to protect from overwrite)
permanently stranded stuck-DRAFTED blueprints — once a render failed, the
pre-download dedup blocked any retry and the blueprint piled up forever
(96 sports DRAFTED stuck mid-2026 was the breaking case). PushToBacklog
still upserts on existing rows for the protect-from-overwrite semantics —
the retry just gets a fresh download attempt instead of being silently
deduped out.

Reads:  context["stories"], context["niche_id"]
Writes: context["stories"] (filtered), context["run_stats"]["pre_download_dedup"]

Non-fatal: if the dedup query fails, all stories pass through and the
push stage still catches duplicates.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any

from genlab_core.http.backlog_client import BacklogClient

# Narrower than push_to_backlog's LIVE_OR_PENDING set: omits DRAFTED + SCORED
# so stuck/stranded blueprints get a chance to re-download on the next run.
# The canonical set lives in :mod:`genlab_core.pipeline.blueprint_status`;
# the subset relationship `LIVE ⊂ LIVE_OR_PENDING` is what encodes the
# different semantics of the two stages.
from genlab_core.pipeline.blueprint_status import LIVE as _PRE_DOWNLOAD_BLOCKING  # noqa: E402
from genlab_core.pipeline.stage_context import StageContext


def _blocks_pre_download(row: dict) -> bool:
    """True if this blueprint should block re-downloading the same content.

    Only live or about-to-be-live rows block; DRAFTED / SCORED can be retried.
    """
    fields = row.get("fields", row)
    return fields.get("status", "") in _PRE_DOWNLOAD_BLOCKING


def _is_within_url_ttl(bp: dict, cutoff: datetime | None) -> bool:
    """True if blueprint is recent enough to still block URL dedup.

    Mirrors the same helper in push_to_backlog.py — see there for the
    fuller "sticky-source-URL" rationale (gaming Twitch directory pages
    + Steam store pages + sports ScoreBat match URLs recur daily, and
    permanently dedup-lock fresh trending fetches without a TTL). When
    ``cutoff`` is None (default), every blueprint blocks forever; with
    a cutoff set, blueprints whose ``created_at`` predates it stop
    contributing to the dedup set, letting today's "Overwatch" run
    re-publish if the last "Overwatch" was >cutoff days ago.

    PR #512 (2026-06-24): the top-level vs fields lookup-trap workaround
    moved to ``record_helpers.record_created_at_dt`` (centralised after
    this same trap bit 3 callers in 24h). Missing or unparseable dates
    → keep in dedup set (conservative — don't risk re-publishing
    unknown-age content).
    """
    if cutoff is None:
        return True
    from genlab_core.storage.record_helpers import record_created_at_dt

    created = record_created_at_dt(bp)
    if created is None:
        return True
    return created >= cutoff


logger = logging.getLogger(__name__)


class PreDownloadDedup:
    """Filter stories against the active-blueprint dedup set before download."""

    def __init__(self) -> None:
        self._client: BacklogClient | None = None

    def _get_client(self) -> BacklogClient:
        if self._client is None:
            self._client = BacklogClient()
        return self._client

    def execute(self, context: StageContext) -> StageContext:
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

        # URL-dedup TTL (added 2026-06-23) — opt-in via niche.yaml
        # ``pipeline.url_dedup_ttl_days``. Same semantics as the helper in
        # push_to_backlog.py: blueprints older than the TTL stop blocking
        # fresh URL fetches, so sticky-source niches (gaming, sports) can
        # republish trending content after the cutoff. Niches without the
        # config keep the old "dedup forever" behaviour.
        _url_dedup_ttl_days = (
            context.get("niche_config", {}).get("pipeline", {}).get("url_dedup_ttl_days")
        )
        _url_dedup_cutoff = (
            datetime.now(UTC) - timedelta(days=_url_dedup_ttl_days)
            if _url_dedup_ttl_days and _url_dedup_ttl_days > 0
            else None
        )

        active_bps = [
            bp
            for bp in recent_bps
            if _blocks_pre_download(bp) and _is_within_url_ttl(bp, _url_dedup_cutoff)
        ]
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

        # 2026-07-23: emit decision trace symmetric with VideoGate +
        # ViralityScoring + QCGates. Fourth stage this session to gain
        # trace emission. Motivating pattern: pipeline_alerts warns
        # about "0 blueprints, stories created but 0 blueprints
        # (dedup?)" — this trace surfaces the dedup drop counts BEFORE
        # the alert fires 30 min later.
        try:
            from genlab_core.observability.decision_trace import record_decision
            from genlab_core.pipeline.reasoning_trace import append_trace

            total_dropped = dropped_url + dropped_vid
            # WARN when all stories were dedup-dropped — the "dedup
            # ate every candidate" state that the zero_blueprints
            # alert catches later.
            if len(stories) > 0 and len(kept) == 0:
                trace_decision = "warning"
            elif total_dropped > len(stories) // 2:
                # More than half dropped — worth noting.
                trace_decision = "info"
            else:
                trace_decision = "info"

            reasons_line = (
                f"kept={len(kept)}/{len(stories)}, "
                f"dropped_url={dropped_url}, dropped_video_id={dropped_vid}"
            )
            metadata = {
                "input_count": len(stories),
                "kept_count": len(kept),
                "dropped_url": dropped_url,
                "dropped_video_id": dropped_vid,
                "active_blueprint_urls": len(seen_url_hashes),
                "active_blueprint_video_ids": len(seen_video_ids),
            }
            append_trace(
                context,
                stage="PreDownloadDedup",
                decision=trace_decision,
                confidence=1.0,
                reasons=[reasons_line],
                metadata=metadata,
            )
            record_decision(
                context,
                stage="PreDownloadDedup",
                decision=trace_decision,
                reason=reasons_line,
                confidence=1.0,
                metadata=metadata,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.warning(
                "[PreDownloadDedup] trace emission failed: %s",
                exc,
                exc_info=True,
            )

        return context
