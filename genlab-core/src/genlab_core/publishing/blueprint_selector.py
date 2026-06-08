"""Pre-publish blueprint selection: query, gatekeeper, top-1 by score.

Single public entry point :func:`select_blueprint` runs the first three
steps of the publish flow:

  1. Query the niche's ``VISUAL_READY`` blueprints from the backlog.
  2. Run the :class:`PublishGatekeeper` on each — applies the 7
     composable gates (approval, schedule, score, media, daily cap)
     plus a cross-niche-leak guard.
  3. Sort the survivors by ``priority_score`` and return the top one
     (or ``None`` if nothing's eligible).

Lives in its own module so the orchestrator's main flow doesn't have
to inline the query + gate + sort dance. Extracted in the refactor-#9
decomposition (PR 6d/N).
"""

from __future__ import annotations

import logging
from typing import Any

from genlab_core.platforms.gatekeeper import PublishGatekeeper
from genlab_core.publishing.niche_validation import normalize_niche

logger = logging.getLogger(__name__)


def select_blueprint(niche_id: str, backlog_client: Any) -> dict | None:
    """Return the single best VISUAL_READY blueprint for ``niche_id``, or
    ``None`` if none pass the gatekeeper.

    The contract is "publish the highest-priority eligible blueprint for
    this niche on this run" — there's no batching, the publisher is
    one-reel-per-niche-per-day by design.
    """
    all_blueprints = backlog_client.get_blueprints_by_status(
        "VISUAL_READY",
        niche_id=niche_id,
    )
    if not all_blueprints:
        logger.info("[publish] No VISUAL_READY blueprints for niche=%s", niche_id)
        return None

    eligible = _filter_by_gatekeeper(all_blueprints, niche_id)
    if not eligible:
        logger.info("[publish] No blueprints passed gatekeeper for niche=%s", niche_id)
        return None

    # Sort by priority_score descending and pick the top.
    eligible.sort(
        key=lambda b: float(b.get("fields", {}).get("priority_score", 0) or 0),
        reverse=True,
    )
    best = eligible[0]
    fields = best.get("fields", best)
    logger.info(
        "[publish] Selected blueprint %s (score=%.2f, hook=%s)",
        best.get("id", "")[:16],
        float(fields.get("priority_score", 0) or 0),
        (fields.get("hook", "") or "")[:50],
    )
    return best


def _filter_by_gatekeeper(blueprints: list[dict], niche_id: str) -> list[dict]:
    """Run the PublishGatekeeper on each blueprint, applying a cross-niche
    safety guard first. Returns the eligible subset.

    The cross-niche guard (Sprint 62) refuses to publish a blueprint whose
    ``niche_id`` doesn't match this run's niche — defence-in-depth against
    a query that accidentally returns rows from another channel.
    """
    gatekeeper = PublishGatekeeper()
    eligible: list[dict] = []
    for bp in blueprints:
        fields = bp.get("fields", bp)
        bp_niche = normalize_niche(fields.get("niche_id", "") or "")
        if bp_niche != niche_id:
            logger.warning(
                "[publish] Skipping blueprint %s: niche mismatch (%s != %s)",
                bp.get("id", "?"),
                bp_niche,
                niche_id,
            )
            continue
        gate = gatekeeper.evaluate(fields, "")  # platform-agnostic evaluation
        if gate.allowed:
            eligible.append(bp)
        else:
            logger.info(
                "[publish] Blueprint %s blocked by %s: %s (title='%s')",
                bp.get("id", "?")[:8],
                gate.gate_name,
                gate.reason,
                (fields.get("title") or "")[:50],
            )
    return eligible
