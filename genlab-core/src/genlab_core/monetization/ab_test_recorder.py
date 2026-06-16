"""CTA variant → ab_tests recorder (producer wire for the half-wired table).

Why this exists
---------------
The 2026-06-16 docs audit caught this: `ab_tests` table has 0 rows
because cta_engine selects + assigns CTA variants to blueprints but
never calls `create_ab_test()` to record which variant won which
blueprint. The CTA bandit (cta_bandit.CTABandit) can't promote winners
or kill losers without that producer-side record.

This module ships the producer side. The consumer side (reward
attribution joining ab_tests to publishing_analytics) is a separate
follow-up — at minimum the table is alive now so analytics can run
ad-hoc queries.

Contract
--------
1. Idempotent — calling twice with the same (niche_id, candidate_id,
   variant_arm_id) writes 2 rows. The producer is expected to fire
   once per blueprint creation, so callers shouldn't double-fire.
2. Best-effort — any DB error is logged at WARNING and swallowed.
   Failing to record an A/B assignment must NEVER block blueprint
   creation (blueprints are the operator-visible output; ab_tests
   is an analytics-only sidecar).
3. The shape mirrors the table:
     niche_id    — blueprint's niche
     test_name   — the variant arm_id (uniquely identifies the
                   variant within its bandit; safe to use as the
                   test identifier given the current single-bandit-
                   per-niche design)
     variant_a   — same as test_name (per-blueprint assignment is
                   1 variant, not 2; legacy A/B schema doesn't quite
                   fit but variant_a stays populated for human
                   readability in ad-hoc queries)
     status      — 'active' (the variant is in flight; downstream
                   reward attribution should flip to 'completed'
                   once reward_48h is recorded)
     extra       — {candidate_id, story_id, recorded_at, platforms}
                   so analytics can join ab_tests to blueprints on
                   the candidate_id field
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


def record_cta_variant_assignment(
    client: Any,
    *,
    niche_id: str,
    candidate_id: str,
    variants_csv: str,
    story_id: str = "",
    platforms_csv: str = "",
) -> int:
    """Write one ab_tests row per variant arm_id used on this blueprint.

    Args:
        client: BacklogClient instance (has .ab_tests proxy).
        niche_id: blueprint's niche (used for RLS isolation).
        candidate_id: the blueprint's candidate_id (the dedup hash;
            unique per blueprint; used as the analytics join key).
        variants_csv: comma-separated arm_ids that cta_engine assigned
            (multiple values mean multi-platform — one variant per
            platform). Empty string is a no-op.
        story_id: optional, the source story's id (carried in extra
            for richer joins later).
        platforms_csv: optional, comma-separated platform names that
            received variants. Useful for "did this variant win on
            youtube specifically?" queries downstream.

    Returns:
        Count of rows actually written. 0 on any short-circuit
        (proxy missing, empty variants, all writes failed).
    """
    if not variants_csv or not variants_csv.strip():
        return 0
    if not niche_id:
        logger.warning("[ab_test] skipping write — empty niche_id")
        return 0

    proxy = getattr(client, "ab_tests", None)
    if proxy is None:
        # Table not configured in this environment (dev / test). No-op
        # — the inject_cta side keeps the variant tag on the blueprint
        # for in-blueprint inspection regardless.
        return 0

    variants = [v.strip() for v in variants_csv.split(",") if v.strip()]
    if not variants:
        return 0

    extra_base = {
        "candidate_id": candidate_id or "",
        "story_id": story_id or "",
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    if platforms_csv:
        extra_base["platforms"] = platforms_csv

    written = 0
    for variant in variants:
        # `create` on the store proxy returns the new row id (or None
        # when the proxy is mis-configured). Failures are logged but
        # don't propagate — analytics-sidecar must never block the
        # publishing path.
        try:
            new_id = client.create_ab_test(
                {
                    "niche_id": niche_id,
                    "test_name": variant,
                    "variant_a": variant,
                    "variant_b": "",
                    "status": "active",
                    "extra": {**extra_base, "variant_arm_id": variant},
                }
            )
            if new_id:
                written += 1
        except Exception as exc:  # noqa: BLE001 — best-effort sidecar
            logger.warning(
                "[ab_test] failed to record variant=%s niche=%s candidate=%s: %s",
                variant,
                niche_id,
                candidate_id[:8] if candidate_id else "(empty)",
                exc,
            )

    if written:
        logger.info(
            "[ab_test] recorded %d/%d variant assignment(s) for niche=%s candidate=%s",
            written,
            len(variants),
            niche_id,
            candidate_id[:8] if candidate_id else "(empty)",
        )
    return written
