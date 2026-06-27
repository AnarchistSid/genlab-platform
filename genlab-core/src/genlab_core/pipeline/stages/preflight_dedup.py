"""Pipeline stage: PreflightDedup — drop stories that ``push_to_backlog``
would dedup-reject anyway, BEFORE the expensive enrich + render path.

## Why this stage exists (prod evidence, 2026-06-27/28)

Gaming's 04:00 UTC run on 2026-06-27 burned ~280 seconds of render CPU
producing 3 videos that were ALL dedup-blocked at ``push_to_backlog``:

    Filter passed 5 stories
    Enrich passed 3 (2 IGDB failures)
    Render took ~279 seconds for 3 videos
    ValidateVideos passed 3
    push_to_backlog blocked all 3 (Overwatch + LoL URLs published; Sand:Raiders
        title near-dupe)
    → 0 blueprints despite 279s of CPU spent

PR #621 stop-gapped this by raising ``filter_top_n`` from 5 → 7 (gives
dedup 2 more survivors at filter stage). This stage is the structural
fix: detect the dedup-blocked stories at the FILTER stage output, drop
them, and let the survivors flow into the expensive pipeline.

## Relationship to PushToBacklog (the final authority)

This stage is an OPTIMIZATION, not a replacement. PushToBacklog stays
as the final dedup authority because:

  * Enrich + writing stages can SYNTHESISE new dedup signals
    (e.g. ``story.video_id`` populated by ExtractGamingMedia from
    IGDB lookup)
  * Two parallel niche pipelines could push the same blueprint between
    this stage running and the push stage running (small window, but
    real)
  * The cross-niche source-prefix guard + ``_skip_llm`` no-hook drop
    + ``content_pool`` cross-niche check all live in push_to_backlog
    and depend on post-writing-stage signals this stage cannot see

The drift-prevention test in ``tests/pipeline/test_dedup_keys.py`` pins
that both consumers use the same dedup logic so a refactor here doesn't
silently diverge from the final authority.

## Relationship to PreDownloadDedup

PreDownloadDedup uses the narrower ``LIVE`` blocking set (excludes
DRAFTED + SCORED) — stuck-DRAFTED blueprints don't block their own
re-download. This stage uses ``LIVE_OR_PENDING`` (includes DRAFTED +
SCORED) because that's what push_to_backlog uses for overwrite
protection; running with the narrower set would let stories through
that push_to_backlog will still block, defeating the purpose.

The trade-off: a stuck-DRAFTED blueprint blocks ITS OWN story at this
stage. This is fine because push_to_backlog's revive path will upsert
the existing row when reached — but we save the render cost.

## Fail-OPEN posture

If the backlog client is missing or the dedup-keys load fails, this
stage logs a warning and passes ALL stories through unchanged.
Downstream push_to_backlog will catch the duplicates (with the cost of
the wasted render). Never let a dedup-keys exception abort the
pipeline.

## Opt-in per niche

Other niches keep their current behaviour unless their ``niche.yaml``
explicitly inserts this stage between FilterXStories and the enrich
stage. Gaming is the first opt-in because its prod evidence is the
clearest; sports + movies + anime + ai_creators are follow-up
candidates once the gaming rollout proves stable.

Reads:  ``context["stories"]``, ``context["niche_id"]``,
        ``context["niche_config"]``, ``context.get("_backlog_client")``
Writes: ``context["stories"]`` (filtered),
        ``context["run_stats"]["preflight_dedup"]``
"""

from __future__ import annotations

import logging
from typing import Any

from genlab_core.http.backlog_client import BacklogClient
from genlab_core.pipeline.dedup_keys import DedupKeys, load_dedup_keys, story_is_blocked
from genlab_core.pipeline.stage_context import StageContext

logger = logging.getLogger(__name__)


class PreflightDedup:
    """Filter stories against the push-to-backlog dedup set before enrich/render."""

    def __init__(self) -> None:
        self._client: BacklogClient | None = None

    def _get_client(self, context: dict[str, Any]) -> BacklogClient:
        """Lazy BacklogClient — same pattern as push_to_backlog._get_client.

        Tests inject ``self._client`` directly; production constructs on
        first call.
        """
        if self._client is None:
            # Honor context["backlog_client_config_path"] if set (tests +
            # the pipeline runner override).
            config_path = (
                context.get("backlog_client_config_path") if isinstance(context, dict) else None
            )
            if config_path:
                self._client = BacklogClient(config_path=config_path)
            else:
                self._client = BacklogClient()
        return self._client

    def execute(self, context: StageContext) -> StageContext:
        stories = context.get("stories", [])
        if not stories:
            # Nothing to filter — leave run_stats empty for parity with
            # PreDownloadDedup's behaviour on empty input.
            return context

        niche_id = context.get("niche_id")
        if not niche_id:
            # Without a niche_id we can't filter the backlog formula;
            # fail-OPEN and let push_to_backlog handle it.
            logger.warning("[PreflightDedup] missing niche_id — passing all stories through")
            return context

        # Fail-OPEN if client init or load fails (mirrors PreDownloadDedup).
        try:
            client = self._get_client(context)
        except Exception as exc:  # noqa: BLE001 — fail-OPEN posture
            logger.warning(
                "[PreflightDedup] BacklogClient init failed — passing all stories through: %s",
                exc,
            )
            return context

        niche_config = context.get("niche_config", {}) if isinstance(context, dict) else {}
        try:
            keys: DedupKeys = load_dedup_keys(
                niche_id=niche_id,
                backlog_client=client,
                niche_config=niche_config,
            )
        except Exception as exc:  # noqa: BLE001 — fail-OPEN
            logger.warning(
                "[PreflightDedup] dedup-keys load failed — passing all stories through: %s",
                exc,
            )
            return context

        survivors: list[dict[str, Any]] = []
        dropped: list[dict[str, str]] = []
        for story in stories:
            blocked, reason = story_is_blocked(story, keys)
            if blocked:
                title_snip = (story.get("title") or "?")[:60]
                dropped.append({"title": title_snip, "reason": reason})
                logger.info(
                    "[PreflightDedup] dropping '%s' (reason: %s)",
                    title_snip,
                    reason,
                )
            else:
                survivors.append(story)

        logger.info(
            "[PreflightDedup] %d → %d after dedup pre-filter (%d dropped)",
            len(stories),
            len(survivors),
            len(dropped),
        )
        context["stories"] = survivors
        context.setdefault("run_stats", {})["preflight_dedup"] = {
            "input": len(stories),
            "survivors": len(survivors),
            "dropped_count": len(dropped),
            "dropped_titles": [d["title"] for d in dropped[:10]],
            "dropped_reasons": [d["reason"] for d in dropped],
            "active_blueprint_count": keys.n_active_blueprints,
            "url_hashes_loaded": len(keys.url_hashes),
            "titles_loaded": len(keys.titles_normalized),
            "video_ids_loaded": len(keys.video_ids),
        }
        return context
