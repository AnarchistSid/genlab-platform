"""Backfill ``blueprints.product_slug`` from existing ``affiliate_product``.

L3 PR 2 (2026-07-07). The schema migration `a8w9x0y1z2a3` added the
``product_slug`` column but left it NULL for pre-existing rows. This
script populates it via ``slugify_product_name`` on
``affiliate_product``.

Rows with ``affiliate_product IS NULL`` stay NULL — no product was
matched, no slug to compute. The partial index
``idx_blueprints_product_slug`` matches this pattern (indexed WHERE
NOT NULL).

Idempotent: safe to run multiple times. Only updates rows where
``product_slug IS NULL`` AND ``affiliate_product IS NOT NULL``.

Usage
-----
Preview:
    python scripts/backfill_product_slug.py --dry-run

Apply (on prod):
    python scripts/backfill_product_slug.py --apply

Exit codes:
    0 — success (or nothing to do)
    1 — DATABASE_URL not set / DB unreachable
    2 — validation error (should never happen; guards)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

logger = logging.getLogger("backfill_product_slug")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write the updates (default is dry-run).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview counts without writing. (Default when --apply is absent.)",
    )
    args = parser.parse_args()
    apply = args.apply and not args.dry_run

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        logger.error("DATABASE_URL not set")
        return 1

    try:
        import psycopg
    except ImportError:
        logger.error("psycopg not installed")
        return 1

    from genlab_core.monetization.affiliate_matcher import slugify_product_name

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            # Count what needs updating
            cur.execute(
                "SELECT count(*) FROM blueprints "
                "WHERE product_slug IS NULL AND affiliate_product IS NOT NULL "
                "AND affiliate_product != ''"
            )
            row = cur.fetchone()
            n_to_update = row[0] if row else 0
            logger.info("blueprints needing product_slug backfill: %d", n_to_update)

            if n_to_update == 0:
                logger.info("Nothing to do.")
                return 0

            # Fetch (id, affiliate_product) for slugification
            cur.execute(
                "SELECT id, affiliate_product FROM blueprints "
                "WHERE product_slug IS NULL AND affiliate_product IS NOT NULL "
                "AND affiliate_product != ''"
            )
            rows = cur.fetchall()

            if not apply:
                # Dry-run: show first 5 examples
                logger.info("[DRY-RUN] Would update %d rows. Sample transformations:", len(rows))
                for bp_id, product_name in rows[:5]:
                    slug = slugify_product_name(product_name)
                    logger.info("  %s: %r → %r", bp_id, product_name, slug)
                logger.info("[DRY-RUN] Re-run with --apply to execute.")
                return 0

            # Apply: batched update
            batch_size = 500
            n_written = 0
            for i in range(0, len(rows), batch_size):
                batch = rows[i : i + batch_size]
                for bp_id, product_name in batch:
                    slug = slugify_product_name(product_name)
                    if not slug:
                        # Defensive: slugify returned empty (unlikely but
                        # possible for e.g. all-unicode names). Skip.
                        continue
                    cur.execute(
                        "UPDATE blueprints SET product_slug = %s WHERE id = %s",
                        (slug, bp_id),
                    )
                    n_written += 1
                conn.commit()
                logger.info("committed batch %d (%d/%d)", i // batch_size, n_written, len(rows))

            logger.info("Backfill complete: %d rows updated", n_written)

    return 0


if __name__ == "__main__":
    sys.exit(main())
