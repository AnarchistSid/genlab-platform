#!/usr/bin/env python3
"""Import Cuelinks V3 /conversions → affiliate_revenue rows (REAL data).

2026-07-17 (Layer 2 batch 2 monetization). Closes the "all revenue is
fake" gap identified in audit round 4:

    Pre-fix (2026-03 → 2026-07-17):
      - Zero conversions ever tracked from any network
      - affiliate_revenue.conversions = 0 on every row
      - affiliate_revenue.revenue_amount = clicks × 0.02 (estimation)
      - 108-arm product bandit had 0 observations
      - proxy_revenue_aggregator wrote every row as fictional

    Post-fix (this script):
      - Polls Cuelinks V3 /conversions/list daily via systemd timer
      - Writes REAL confirmed conversions with real commission amounts
      - Sub-ID attribution routes back to blueprint_id
      - Idempotent via extra->>'conversion_id' check
      - Product bandit finally receives real observations

## Operator workflow

1. Set `CUELINKS_V3_API_KEY` on prod `.env` (still pending as of ship)
2. Timer fires daily at 06:15 UTC (~5 min after publisher completes)
3. Fetches conversions from yesterday + today (2-day window to catch
   late-confirmed conversions from earlier orders)
4. Writes new rows; skips already-imported conversion_ids

## Idempotence

Each Cuelinks conversion has a stable `conversion_id`. Script SELECTs
`affiliate_revenue WHERE network='cuelinks' AND extra->>'conversion_id' = X`
before INSERT. Duplicates skip silently.

## SubID routing

Cuelinks V3 /links/convert accepts a `subid` param that propagates
to conversion records. Gen Lab's convention (per
cuelinks_client.convert_url docstring):
    subid = f"{niche_id}:{blueprint_id[:8]}"
Import parses subid to route the conversion back to the specific
blueprint that drove the click.

## Exit codes

    0 — success (conversions imported, or no new conversions to import)
    1 — DATABASE_URL unset / DB unreachable / API key unset
    2 — insertion failure
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import UTC, datetime, timedelta

logger = logging.getLogger("import_cuelinks_conversions")


def _parse_subid(subid: str) -> tuple[str | None, str | None]:
    """Parse Gen Lab's convention `{niche_id}:{blueprint_prefix}`.

    Returns (niche_id, blueprint_prefix). Both None if subid doesn't
    match the convention (e.g., legacy Cuelinks clicks made outside
    Gen Lab or subid was empty at click time).
    """
    if not subid or ":" not in subid:
        return None, None
    parts = subid.split(":", 1)
    if len(parts) != 2:
        return None, None
    niche_id = parts[0].strip() or None
    bp_prefix = parts[1].strip() or None
    return niche_id, bp_prefix


def _resolve_blueprint_id(cur, niche_id: str | None, bp_prefix: str | None) -> str | None:
    """Match subid's blueprint_prefix (first 8 chars) back to a
    full blueprint UUID via prefix search on blueprints.id.

    Returns None if prefix doesn't uniquely identify one blueprint
    (0 or >1 matches). Ambiguity is rare — 8 hex chars gives 4B
    possible prefixes; we've published <2K blueprints lifetime.
    """
    if not niche_id or not bp_prefix or len(bp_prefix) < 6:
        return None
    try:
        cur.execute(
            """
            SELECT id::text FROM blueprints
            WHERE id::text LIKE %s
              AND niche_id = %s
            LIMIT 2
            """,
            (bp_prefix + "%", niche_id),
        )
        rows = cur.fetchall()
        if len(rows) == 1:
            return rows[0][0]
    except Exception as exc:
        logger.debug(
            "[import-cuelinks] blueprint prefix resolve failed for %s:%s: %s",
            niche_id, bp_prefix, exc,
        )
    return None


def _already_imported(cur, conversion_id: str) -> bool:
    """Idempotency check — has this conversion_id already been imported?"""
    if not conversion_id:
        return False
    try:
        cur.execute(
            """
            SELECT 1 FROM affiliate_revenue
            WHERE network = 'cuelinks'
              AND extra->>'conversion_id' = %s
            LIMIT 1
            """,
            (conversion_id,),
        )
        return cur.fetchone() is not None
    except Exception as exc:
        logger.debug(
            "[import-cuelinks] idempotency check failed for %s: %s",
            conversion_id, exc,
        )
        return False


def _insert_conversion(cur, conv: dict, blueprint_id: str | None, niche_id: str | None) -> bool:
    """Insert one conversion row. Returns True if inserted, False on error."""
    import json

    try:
        cur.execute(
            """
            INSERT INTO affiliate_revenue (
                niche_id, blueprint_id, network, product_id,
                clicks, conversions, revenue_amount, currency, date, extra
            ) VALUES (%s, %s, 'cuelinks', %s, 0, 1, %s, %s, %s, %s::jsonb)
            """,
            (
                niche_id or "unknown",
                blueprint_id,
                conv.get("campaign_name") or None,
                conv.get("commission_amount", 0.0),
                conv.get("currency", "INR"),
                conv.get("conversion_time", "")[:10] or datetime.now(UTC).date().isoformat(),
                json.dumps(
                    {
                        "conversion_id": conv.get("conversion_id"),
                        "order_id": conv.get("order_id"),
                        "subid": conv.get("subid"),
                        "sale_amount": conv.get("sale_amount"),
                        "status": conv.get("status"),
                    }
                ),
            ),
        )
        return True
    except Exception as exc:
        logger.warning(
            "[import-cuelinks] insert failed for conversion_id=%s: %s",
            conv.get("conversion_id"), exc,
        )
        return False


def run(*, from_date: str, to_date: str, apply: bool) -> int:
    """Fetch conversions in [from_date, to_date] and import new ones.

    Returns process exit code (0=ok, 1=setup error, 2=insert error).
    """
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        logger.error("[import-cuelinks] DATABASE_URL unset — cannot import")
        return 1

    try:
        from genlab_core.monetization.cuelinks_client import list_conversions
    except ImportError as exc:
        logger.error("[import-cuelinks] cuelinks_client import failed: %s", exc)
        return 1

    conversions = list_conversions(from_date=from_date, to_date=to_date)
    logger.info(
        "[import-cuelinks] fetched %d conversions from Cuelinks V3 for %s to %s",
        len(conversions), from_date, to_date,
    )
    if not conversions:
        return 0

    if not apply:
        for conv in conversions[:10]:
            logger.info(
                "[import-cuelinks] DRY-RUN would import conv_id=%s subid=%s commission=%s %s",
                conv.get("conversion_id"),
                conv.get("subid"),
                conv.get("commission_amount"),
                conv.get("currency"),
            )
        return 0

    try:
        import psycopg
    except ImportError:
        logger.error("[import-cuelinks] psycopg not installed")
        return 1

    n_inserted = 0
    n_skipped = 0
    n_failed = 0

    try:
        with psycopg.connect(dsn, connect_timeout=10) as conn:
            with conn.cursor() as cur:
                for conv in conversions:
                    conv_id = conv.get("conversion_id", "")
                    if not conv_id:
                        n_skipped += 1
                        continue

                    if _already_imported(cur, conv_id):
                        n_skipped += 1
                        continue

                    niche_id, bp_prefix = _parse_subid(conv.get("subid", ""))
                    blueprint_id = _resolve_blueprint_id(cur, niche_id, bp_prefix)

                    if _insert_conversion(cur, conv, blueprint_id, niche_id):
                        n_inserted += 1
                    else:
                        n_failed += 1
                conn.commit()
    except Exception as exc:
        logger.error("[import-cuelinks] DB error: %s", exc)
        return 1

    logger.info(
        "[import-cuelinks] Done: inserted=%d skipped_duplicate=%d failed=%d",
        n_inserted, n_skipped, n_failed,
    )
    return 2 if n_failed > 0 and n_inserted == 0 else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write to DB (default: dry-run)",
    )
    parser.add_argument(
        "--days-back",
        type=int,
        default=2,
        help="Fetch window: last N days (default 2 — catches late-confirmed)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    to_date = datetime.now(UTC).date()
    from_date = to_date - timedelta(days=args.days_back)

    return run(
        from_date=from_date.isoformat(),
        to_date=to_date.isoformat(),
        apply=args.apply,
    )


if __name__ == "__main__":
    sys.exit(main())
