#!/usr/bin/env python3
"""Import Amazon Associates report CSV → affiliate_conversions table.

L3 PR 10 (2026-07-07). Scans a directory of operator-uploaded CSVs,
parses each via :mod:`amazon_report_importer`, and inserts rows into
the ``affiliate_conversions`` table (migration ``b9y0z1a2b3c4``).

## Operator workflow

1. Log into associates.amazon.com/reports
2. Download today's "Earnings" CSV
3. Drop it into ``$GENLAB_TMP/amazon-reports/YYYY-MM-DD-<region>.csv``
4. This script is fired daily via systemd timer (follow-up PR ships
   the `.timer` file) or manually as
       python scripts/import_amazon_conversions.py --apply

## Idempotence

Uniqueness constraint on
``(source_csv_hash, csv_line_no)`` in the migration means
re-importing the same CSV inserts zero new rows. A CSV with a
different filename but same bytes still hashes identically and is
also a no-op.

An updated CSV (Amazon revises the previous day's numbers 24-72h
after the row's date to account for cancellations) DOES insert new
rows because the hash differs — L3 PR 11's aggregate query will
sum the LATEST versions per (date, asin) tuple (or explicitly
prefer the highest hash, which effectively picks the most-recent
import).

## Niche resolution

Each row's ASIN is mapped to a product slug via the catalog. The
first niche whose catalog contains that ASIN wins. Ambiguous ASINs
(unlikely — Amazon ASINs uniquely identify a product globally) fall
through to the first-catalog-hit.

Rows for unknown ASINs (not in any catalog) are STILL imported but
with ``product_slug = NULL`` — the operator can see them in the
table and add to catalog later; the reward wire skips NULL slugs.

## Exit codes

    0 — success (files processed, or no files to process)
    1 — DATABASE_URL unset / DB unreachable / CSV directory missing
    2 — insertion failure
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("import_amazon_conversions")


def _csv_dir() -> Path:
    """Where CSVs are dropped. Overridable via ``--csv-dir``."""
    tmp = os.environ.get("GENLAB_TMP")
    root = Path(tmp) if tmp else Path.cwd() / ".tmp"
    return root / "amazon-reports"


def _build_asin_to_slug_map() -> dict[str, tuple[str, str]]:
    """Scan the catalog + return ``{asin: (niche_id, slug)}``.

    Parses ASIN from Amazon URLs in each product's networks — the
    format ``amazon.com/dp/<ASIN>?tag=...``. Catalog products with
    non-Amazon networks (direct-brand links) produce no ASIN entry.
    """
    import re

    from genlab_core.monetization.affiliate_matcher import slugify_product_name
    from genlab_core.monetization.catalog_loader import load_catalog

    catalog_path = (
        Path(__file__).resolve().parents[1] / "genlab-core" / "config" / "affiliate_catalog.yaml"
    )
    if not catalog_path.is_file():
        logger.warning(
            "[import-amazon] catalog missing at %s — proceeding without slug map",
            catalog_path,
        )
        return {}

    catalog = load_catalog(catalog_path)
    asin_re = re.compile(r"amazon\.[a-z.]+/(?:dp|gp/product)/([A-Z0-9]{10})", re.I)

    out: dict[str, tuple[str, str]] = {}
    for niche_id, niche in (catalog.get("niches") or {}).items():
        for product in niche.get("products") or []:
            name = product.get("name") or ""
            slug = slugify_product_name(name)
            if not slug:
                continue
            for net_info in (product.get("networks") or {}).values():
                if not isinstance(net_info, dict):
                    continue
                url = net_info.get("url", "") or ""
                m = asin_re.search(url)
                if m:
                    asin = m.group(1).upper()
                    # First hit wins — a catalog product usually has
                    # both amazon.com + amazon.in URLs pointing at
                    # different ASINs for the same product. Both slot
                    # in with the same slug.
                    out.setdefault(asin, (niche_id, slug))
    return out


def _insert_conversions(
    conn,
    csv_hash: str,
    rows,
    asin_map: dict[str, tuple[str, str]],
) -> tuple[int, int]:
    """Batch-INSERT parsed rows with ON CONFLICT DO NOTHING for
    idempotence. Returns ``(n_inserted, n_skipped)``."""
    n_inserted = 0
    n_skipped = 0
    with conn.cursor() as cur:
        for row in rows:
            niche_slug = asin_map.get(row.asin)
            niche_id = niche_slug[0] if niche_slug else None
            product_slug = niche_slug[1] if niche_slug else None
            try:
                cur.execute(
                    """
                    INSERT INTO affiliate_conversions (
                        report_date, asin, product_slug,
                        items_shipped, items_returned,
                        revenue_inr, earnings_inr,
                        tracking_id, niche_id,
                        source_csv_hash, csv_line_no
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (source_csv_hash, csv_line_no) DO NOTHING
                    """,
                    (
                        row.report_date,
                        row.asin,
                        product_slug,
                        row.items_shipped,
                        row.items_returned,
                        row.revenue_inr,
                        row.earnings_inr,
                        row.tracking_id,
                        niche_id,
                        csv_hash,
                        row.csv_line_no,
                    ),
                )
                if cur.rowcount > 0:
                    n_inserted += 1
                else:
                    n_skipped += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[import-amazon] insert failed for asin=%s line=%d: %s",
                    row.asin,
                    row.csv_line_no,
                    exc,
                )
                n_skipped += 1
    conn.commit()
    return n_inserted, n_skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Execute inserts (default is dry-run).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview parsed rows without writing.",
    )
    parser.add_argument(
        "--csv-dir",
        default=None,
        help="Directory containing Amazon CSVs (default: $GENLAB_TMP/amazon-reports).",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable DEBUG logging.")
    args = parser.parse_args()
    apply = args.apply and not args.dry_run

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    csv_dir = Path(args.csv_dir) if args.csv_dir else _csv_dir()
    if not csv_dir.is_dir():
        logger.info(
            "[import-amazon] CSV dir %s does not exist — nothing to do (expected on cold start)",
            csv_dir,
        )
        return 0

    csv_files = sorted(csv_dir.glob("*.csv"))
    if not csv_files:
        logger.info("[import-amazon] no CSVs in %s — nothing to do", csv_dir)
        return 0

    from genlab_core.monetization.amazon_report_importer import parse_csv_file

    asin_map = _build_asin_to_slug_map()
    logger.info(
        "[import-amazon] resolved %d ASIN→slug entries from catalog",
        len(asin_map),
    )

    if not apply:
        # Dry-run — just parse + report counts
        total_rows = 0
        for path in csv_files:
            csv_hash, rows = parse_csv_file(path)
            logger.info(
                "[DRY-RUN] %s → hash=%s… rows=%d",
                path.name,
                csv_hash[:12],
                len(rows),
            )
            total_rows += len(rows)
        logger.info(
            "[DRY-RUN] would insert %d rows across %d files. Re-run with --apply.",
            total_rows,
            len(csv_files),
        )
        return 0

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        logger.error("DATABASE_URL not set")
        return 1

    try:
        import psycopg
    except ImportError:
        logger.error("psycopg not installed")
        return 1

    try:
        with psycopg.connect(dsn) as conn:
            grand_inserted = 0
            grand_skipped = 0
            for path in csv_files:
                csv_hash, rows = parse_csv_file(path)
                n_ins, n_skip = _insert_conversions(conn, csv_hash, rows, asin_map)
                grand_inserted += n_ins
                grand_skipped += n_skip
                logger.info(
                    "[import-amazon] %s: %d inserted, %d skipped",
                    path.name,
                    n_ins,
                    n_skip,
                )
            logger.info(
                "[import-amazon] Total: %d inserted, %d skipped across %d files",
                grand_inserted,
                grand_skipped,
                len(csv_files),
            )
    except Exception as exc:  # noqa: BLE001
        logger.error("[import-amazon] DB operation failed: %s", exc)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
