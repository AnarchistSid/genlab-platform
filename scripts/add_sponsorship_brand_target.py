#!/usr/bin/env python3
"""Phase 3.C session 1 — brand target seeding CLI.

Operator's tool for adding a brand to ``sponsorship_brand_targets``.
Auto-outreach reads from that table; without seeded rows, the
weekly generator writes zero drafts.

## Design

Deliberately CLI-only in session 1 (no dashboard UI). Rationale:

  * Adding a brand target is a low-frequency operator action —
    dashboard UI overhead not justified for the current expected
    cadence (~5-10 brands/niche/month).
  * CLI forces the operator to think about the brand + email +
    contact_first_name explicitly — reduces risk of "batch-import
    a scraped list and then auto-outreach to random emails."
  * If session 2 or later observation reveals we need faster
    seeding, a bulk-import CSV endpoint is a small follow-up.

## Usage

    uv run python scripts/add_sponsorship_brand_target.py \\
      --niche gaming \\
      --brand "AcmeGaming" \\
      --email "sarah@acmegaming.com" \\
      --contact "Sarah" \\
      --website "https://acmegaming.com" \\
      --notes "reached out at GDC 2026"

    # List active targets for a niche
    uv run python scripts/add_sponsorship_brand_target.py \\
      --list --niche gaming

    # Deactivate (soft-delete) a target — never hard-delete because
    # the pipeline table has an FK reference; deactivating stops
    # future drafts without corrupting history.
    uv run python scripts/add_sponsorship_brand_target.py \\
      --deactivate --niche gaming --email sarah@acmegaming.com

## Exit codes

  * 0 — success
  * 1 — DATABASE_URL unset OR required field missing
  * 2 — duplicate (niche, email) — unique constraint violation
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

logger = logging.getLogger("add_sponsorship_brand_target")

_VALID_NICHES = frozenset({"ai_creators", "anime", "gaming", "movies", "sports"})


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--niche", required=True, choices=sorted(_VALID_NICHES))
    ap.add_argument("--brand", default=None,
                    help="Brand display name")
    ap.add_argument("--email", default=None,
                    help="Brand contact email")
    ap.add_argument("--contact", default=None,
                    help="First name of the contact person (used in greeting)")
    ap.add_argument("--website", default=None)
    ap.add_argument("--notes", default=None)
    ap.add_argument("--list", action="store_true",
                    help="List active targets for --niche and exit")
    ap.add_argument("--deactivate", action="store_true",
                    help="Deactivate the (niche, email) target instead of adding")
    ap.add_argument("--added-by", default=os.environ.get("USER", "operator"))
    return ap.parse_args(argv)


def _list_targets(conn, niche_id: str) -> int:
    rows = conn.execute(
        """
        SELECT brand_name, brand_email, contact_first_name, added_at
        FROM sponsorship_brand_targets
        WHERE niche_id = %s AND active = TRUE
        ORDER BY added_at ASC
        """,
        (niche_id,),
    ).fetchall()
    print(f"Active brand targets for {niche_id} ({len(rows)}):")
    for r in rows:
        brand = r.get("brand_name") if hasattr(r, "get") else r[0]
        email = r.get("brand_email") if hasattr(r, "get") else r[1]
        contact = r.get("contact_first_name") if hasattr(r, "get") else r[2]
        print(f"  {brand:<30} {email:<40} {contact or '(no contact)'}")
    return 0


def _deactivate_target(conn, niche_id: str, email: str) -> int:
    result = conn.execute(
        """
        UPDATE sponsorship_brand_targets
        SET active = FALSE
        WHERE niche_id = %s AND brand_email = %s AND active = TRUE
        """,
        (niche_id, email),
    )
    conn.commit()
    if result.rowcount == 0:
        print(f"No active target found for niche={niche_id} email={email}")
        return 1
    print(f"Deactivated: niche={niche_id} email={email}")
    return 0


def _insert_target(
    conn, niche_id: str, brand: str, email: str, contact: str | None,
    website: str | None, notes: str | None, added_by: str,
) -> int:
    try:
        conn.execute(
            """
            INSERT INTO sponsorship_brand_targets
              (niche_id, brand_name, brand_email, contact_first_name,
               website_url, notes, added_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (niche_id, brand, email, contact, website, notes, added_by),
        )
        conn.commit()
    except Exception as exc:
        # UNIQUE(niche_id, brand_email) is the most likely error —
        # surface it explicitly so the operator knows to use
        # --deactivate + re-add instead of silently getting an
        # opaque IntegrityError.
        msg = str(exc)
        if "unique" in msg.lower() or "duplicate" in msg.lower():
            print(f"Duplicate: (niche={niche_id}, email={email}) already exists")
            print("  Use --deactivate then re-add if updating contact info")
            return 2
        print(f"Insert failed: {exc}")
        return 1
    print(f"Added: {brand} <{email}> for niche={niche_id}")
    return 0


def main(argv=None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        logger.error("DATABASE_URL unset")
        return 1

    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        if args.list:
            return _list_targets(conn, args.niche)
        if args.deactivate:
            if not args.email:
                logger.error("--deactivate requires --email")
                return 1
            return _deactivate_target(conn, args.niche, args.email)
        # Default: add
        if not args.brand or not args.email:
            logger.error("Add requires --brand AND --email")
            return 1
        return _insert_target(
            conn, args.niche, args.brand, args.email,
            args.contact, args.website, args.notes, args.added_by,
        )


if __name__ == "__main__":
    sys.exit(main())
