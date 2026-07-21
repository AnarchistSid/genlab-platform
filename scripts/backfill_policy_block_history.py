"""One-shot backfill — reconstruct L1 wire output for pre-deploy
Meta policy_block failures (2026-07-21).

Context: the policy-block learning loop L1 wire (`parallel_publish.
_record_policy_block_event`) shipped in commit `65f07548` and
deployed to prod on 2026-07-21 late. Every FUTURE POLICY_BLOCK
failure now writes a `platform_policy_block` compliance_events row.

But 6 real policy_block failures occurred on 2026-07-18 and
2026-07-19 (all Facebook, all niches: sports, gaming, ai_creators,
anime) — those blueprints exist in prod DB but have NO corresponding
compliance_events row because L1 wasn't yet live.

This script recovers them so L2 (`analyze_recent_policy_blocks`) has
real training samples immediately, bypassing the 1-2 week wait for
new blocks to accumulate under L1's live capture.

## Contract

Uses `log_compliance_event` — the same helper live L1 calls. That way
the historical rows are indistinguishable from live ones (same schema,
same enum validation, same fail-open contract). L2's downstream reader
doesn't care about the distinction.

## Idempotency

Re-running this script would create DUPLICATE rows. Guarded by
`--dry-run` default AND an existence check: skip any blueprint that
already has a `platform_policy_block` row (matched by blueprint_id).

## Usage

    # From /opt/genlab on prod:
    python3 scripts/backfill_policy_block_history.py --dry-run
    python3 scripts/backfill_policy_block_history.py --commit
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

logger = logging.getLogger("backfill_policy_block_history")
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


# The 6 known pre-deploy Meta code=368 failures (queried from
# publishing_analytics 2026-07-21). Hard-coded for auditability —
# a query-driven backfill would need JOIN logic that's easy to
# get wrong; the explicit list means every row is deliberate.
HISTORICAL_BLOCKS: list[dict[str, str]] = [
    {
        "blueprint_id": "a95f30eb-045e-4dcf-8113-67e0ec2bbfe3",
        "niche_id": "sports",
        "platform": "facebook",
        "created_at": "2026-07-19T06:43:51+00:00",
    },
    {
        "blueprint_id": "97a424b8-2060-400b-b815-6cd75206872c",
        "niche_id": "gaming",
        "platform": "facebook",
        "created_at": "2026-07-19T06:41:56+00:00",
    },
    {
        "blueprint_id": "64ae5b4e-322f-4c8e-a22f-44daee240b75",
        "niche_id": "ai_creators",
        "platform": "facebook",
        "created_at": "2026-07-19T06:37:37+00:00",
    },
    {
        "blueprint_id": "f05a86da-926b-4a8f-a1c0-fc0466ff91b9",
        "niche_id": "anime",
        "platform": "facebook",
        "created_at": "2026-07-18T06:47:30+00:00",
    },
    {
        "blueprint_id": "7390f676-fb92-4bd4-a0a6-8c97907017ac",
        "niche_id": "sports",
        "platform": "facebook",
        "created_at": "2026-07-18T06:39:20+00:00",
    },
    {
        "blueprint_id": "bc7134f3-0017-482d-be99-7438be7ac591",
        "niche_id": "gaming",
        "platform": "facebook",
        "created_at": "2026-07-18T06:36:31+00:00",
    },
]

# The exact code=368 error message Meta returned on all 6. Same
# string operators would see in publishing_analytics.error_message
# — L2's LLM judge reads this via metadata.error_snippet.
_ERROR_SNIPPET = (
    "code=368: You're temporarily blocked from using this feature "
    "because you shared something that isn't allowed on Facebook. "
    "Learn More."
)


def _fetch_blueprint_content(conn, blueprint_id: str) -> dict:
    """Pull the content features L1 would have captured (hook,
    caption, hashtag_count, video_url presence)."""
    row = conn.execute(
        """
        SELECT hook, caption, video_url
        FROM blueprints
        WHERE id = %s
        """,
        (blueprint_id,),
    ).fetchone()
    if not row:
        return {"hook": "", "caption": "", "video_url": None}
    if isinstance(row, dict):
        return {"hook": row.get("hook") or "", "caption": row.get("caption") or "",
                "video_url": row.get("video_url")}
    return {"hook": row[0] or "", "caption": row[1] or "", "video_url": row[2]}


def _already_backfilled(conn, blueprint_id: str) -> bool:
    """True iff a platform_policy_block row already exists for this
    blueprint. Prevents duplicate rows on re-run."""
    row = conn.execute(
        """
        SELECT 1 FROM compliance_events
        WHERE blueprint_id = %s
          AND event_type = 'platform_policy_block'
        LIMIT 1
        """,
        (blueprint_id,),
    ).fetchone()
    return row is not None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true",
                        help="Actually write. Default is dry-run.")
    args = parser.parse_args()

    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        logger.error("DATABASE_URL not set — cannot connect")
        return 2

    # Lazy import — script may be invoked from a non-genlab-installed
    # environment (e.g. a fresh SSH shell) and should give a clean
    # error rather than a stack trace.
    try:
        from genlab_core.compliance.events import log_compliance_event
        from genlab_core.storage.tenant_context import pg_connect
    except ImportError as exc:
        logger.error("genlab_core import failed: %s", exc)
        return 2

    inserts_planned = 0
    inserts_done = 0
    skipped_existing = 0
    skipped_missing = 0

    with pg_connect(dsn, niche_id="all", connect_timeout=10) as conn:
        for entry in HISTORICAL_BLOCKS:
            bp_id = entry["blueprint_id"]
            niche_id = entry["niche_id"]
            platform = entry["platform"]

            if _already_backfilled(conn, bp_id):
                logger.info("[skip] %s already has platform_policy_block row", bp_id)
                skipped_existing += 1
                continue

            content = _fetch_blueprint_content(conn, bp_id)
            if not content["hook"] and not content["caption"]:
                logger.warning(
                    "[skip] %s: blueprint has no hook/caption — cannot reconstruct features",
                    bp_id,
                )
                skipped_missing += 1
                continue

            hook = content["hook"][:120]
            caption = content["caption"][:280]
            hashtag_count = caption.count("#")
            has_video_url = bool(content["video_url"])

            metadata = {
                "error_snippet": _ERROR_SNIPPET,
                "hook": hook,
                "caption_fragment": caption,
                "hashtag_count": hashtag_count,
                "has_video_url": has_video_url,
                # Backfill provenance marker — L2 could opt to weight
                # backfilled rows differently, but for MVP they count
                # the same as live rows.
                "backfilled": True,
                "original_created_at": entry["created_at"],
            }

            inserts_planned += 1
            logger.info(
                "[plan] niche=%s platform=%s hook=%r hashtags=%d has_url=%s",
                niche_id, platform, hook[:60], hashtag_count, has_video_url,
            )

            if args.commit:
                ok = log_compliance_event(
                    niche_id=niche_id,
                    event_type="platform_policy_block",
                    decision="block",
                    blueprint_id=bp_id,
                    platform=platform,
                    reasons=["platform_policy_block", "backfilled_from_publishing_analytics"],
                    metadata=metadata,
                )
                if ok:
                    inserts_done += 1
                    logger.info("[done] wrote row for %s", bp_id)
                else:
                    logger.warning("[fail] log_compliance_event returned False for %s", bp_id)

    logger.info(
        "SUMMARY: planned=%d done=%d skip_existing=%d skip_missing=%d dry_run=%s",
        inserts_planned, inserts_done, skipped_existing, skipped_missing,
        not args.commit,
    )
    if not args.commit and inserts_planned:
        logger.info("Re-run with --commit to actually write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
