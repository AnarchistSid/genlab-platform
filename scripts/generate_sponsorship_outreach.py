#!/usr/bin/env python3
"""Phase 3.C session 1 — sponsorship outreach draft generator.

Weekly runner that materializes DRAFTED rows in
``sponsorship_pipeline`` for each active (niche, brand_target) pair
whose niche tier is Bronze/eligible_now/within_2_months/within_6_months.

## What this script DOES

  1. Loads active brand targets from ``sponsorship_brand_targets``
     for niches whose tier is >= tracking. Skips (target, week)
     pairs that already have a DRAFTED/APPROVED/SENT row this week
     (dedup — no duplicate drafts).
  2. Computes tier + audience via the existing
     ``dashboard.server.api.sponsorship_readiness`` helpers.
  3. Builds subject + body via
     ``dashboard.server.api.outreach_template._build_subject`` +
     ``_build_template_body``. Placeholders ``[BRAND]`` and ``[NAME]``
     get filled in from the brand_target row + operator's name env.
  4. Persists DRAFTED row. Operator reviews via
     ``GET /api/v1/sponsorship/pipeline`` and approves via
     ``POST /api/v1/sponsorship/pipeline/<id>/approve``.

## What this script does NOT do

Session 1 explicitly does not send emails. That wire lives in
session 2 (after operator picks SendGrid vs Outlook transport).
The runner writes DRAFTED rows only — moves to SENT is a manual
operator action for now, then auto-send comes session 2.

## Cost

Zero external calls. Reads a few DB tables + generates local strings.

## Usage

    uv run python scripts/generate_sponsorship_outreach.py
    uv run python scripts/generate_sponsorship_outreach.py --dry-run
    uv run python scripts/generate_sponsorship_outreach.py --niche gaming

## Exit codes

  * 0 — completed (rows written OR dry-run OR nothing eligible)
  * 1 — DATABASE_URL unset
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import UTC, datetime, timedelta

logger = logging.getLogger("generate_sponsorship_outreach")

ACTIVE_NICHES = ("ai_creators", "anime", "gaming", "movies", "sports")

# Tiers eligible for auto-outreach draft. ``tracking`` niches don't
# get pitched — no message would be truthful yet.
_ELIGIBLE_TIERS = frozenset({
    "eligible_now", "within_2_months", "within_6_months",
})

# Deduplication window: don't draft a second outreach to the same
# brand within N days of any prior DRAFTED/APPROVED/SENT row. Protects
# against operator seeing duplicate drafts if the timer double-fires,
# and against pestering a brand that just got pitched.
_DEDUP_WINDOW_DAYS = 21


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--niche", default=None,
                    help="Limit to one niche")
    return ap.parse_args(argv)


def _load_brand_targets(conn, niche_id: str) -> list[dict]:
    """Active brand targets for one niche. Fail-open — empty list on
    any query error so a broken row doesn't kill the whole run."""
    try:
        rows = conn.execute(
            """
            SELECT id, brand_name, brand_email, contact_first_name,
                   website_url, notes
            FROM sponsorship_brand_targets
            WHERE niche_id = %s AND active = TRUE
            ORDER BY added_at ASC
            """,
            (niche_id,),
        ).fetchall()
    except Exception as exc:
        logger.warning("[outreach] brand_targets query failed niche=%s: %s",
                       niche_id, exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return []
    return [
        {
            "id": r.get("id") if hasattr(r, "get") else r[0],
            "brand_name": r.get("brand_name") if hasattr(r, "get") else r[1],
            "brand_email": r.get("brand_email") if hasattr(r, "get") else r[2],
            "contact_first_name": (
                r.get("contact_first_name") if hasattr(r, "get") else r[3]
            ),
            "website_url": r.get("website_url") if hasattr(r, "get") else r[4],
            "notes": r.get("notes") if hasattr(r, "get") else r[5],
        }
        for r in rows or []
    ]


def _target_already_drafted_recent(conn, target_id) -> bool:
    """True if this brand has a live (DRAFTED/APPROVED/SENT) row
    within the dedup window. Fail-open to False so a dedup query
    failure doesn't block generation (operator will spot the dup)."""
    try:
        row = conn.execute(
            """
            SELECT 1
            FROM sponsorship_pipeline
            WHERE target_id = %s
              AND status IN ('DRAFTED', 'APPROVED', 'SENT')
              AND drafted_at >= NOW() - (%s || ' days')::INTERVAL
            LIMIT 1
            """,
            (target_id, _DEDUP_WINDOW_DAYS),
        ).fetchone()
        return row is not None
    except Exception as exc:
        logger.warning("[outreach] dedup check failed target=%s: %s",
                       target_id, exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def _tier_and_body_for_niche(niche_id: str, sender_name: str):
    """Compute (tier, subject, body, kit_url) for a niche via the
    dashboard helpers. Returns None on any exception so a broken
    niche doesn't kill the whole run.

    The dashboard helpers read the same SharePoint/audience data
    the Mission Control cards do — so the subject + body match
    exactly what the operator sees when they click "Copy" on the
    SponsorshipReadinessCard row."""
    try:
        from server.api.outreach_template import (
            _build_subject,
            _build_template_body,
            _build_audience_summary,
            _NICHE_DISPLAY,
        )
        from server.api.sponsorship_readiness import _compute_tier
    except ImportError as exc:
        logger.warning("[outreach] dashboard helpers unimportable: %s", exc)
        return None
    try:
        # dashboard helper reads the SP + metric data
        audience = _build_audience_summary(niche_id)
        tier, _nearest = _compute_tier(
            audience.get("platforms", {}),
            audience.get("all_metrics", []),
        )
    except Exception as exc:
        logger.warning("[outreach] tier compute failed niche=%s: %s",
                       niche_id, exc)
        return None
    if tier not in _ELIGIBLE_TIERS:
        return None
    kit_url = os.environ.get(
        "GENLAB_MEDIA_KIT_BASE_URL",
        "https://dashboard.genlab.local",
    ) + f"/media-kit/{niche_id}"
    subject = _build_subject(niche_id, tier)
    body_template = _build_template_body(
        niche_id, audience.get("platforms_flat", []), tier, kit_url,
    )
    return tier, subject, body_template, kit_url


def _personalize(
    body_template: str, brand_name: str, contact_first_name: str | None,
    sender_name: str,
) -> str:
    """Fill in the [BRAND] / [NAME] / greeting placeholders. Falls
    back to brand_name if contact_first_name is missing or blank so
    we never ship "Hi ," (an empty greeting reads worse than raw
    [BRAND]).

    Whitespace-only guard: strip FIRST, then check truthiness. The
    naive ``if x else fallback`` treats "   " as truthy and produces
    an empty greeting after .strip() — pin test
    ``test_whitespace_only_contact_falls_back`` catches this."""
    stripped = (contact_first_name or "").strip()
    greeting_target = stripped if stripped else brand_name
    return (
        body_template
        .replace("Hi [BRAND],", f"Hi {greeting_target},")
        .replace("[BRAND]", brand_name)
        .replace("[NAME]", sender_name)
    )


def _persist_draft(
    conn, niche_id: str, target_id, tier: str, subject: str,
    body: str, kit_url: str,
) -> bool:
    """Insert one DRAFTED row. Returns True on success."""
    try:
        conn.execute(
            """
            INSERT INTO sponsorship_pipeline
              (target_id, niche_id, tier_at_generation, subject, body,
               kit_url, status)
            VALUES (%s, %s, %s, %s, %s, %s, 'DRAFTED')
            """,
            (target_id, niche_id, tier, subject, body, kit_url),
        )
        return True
    except Exception as exc:
        logger.warning("[outreach] persist failed niche=%s target=%s: %s",
                       niche_id, target_id, exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def _generate_niche(
    conn, niche_id: str, sender_name: str, dry_run: bool,
) -> int:
    """Generate DRAFTED rows for one niche. Returns count written."""
    tbb = _tier_and_body_for_niche(niche_id, sender_name)
    if tbb is None:
        print(f"  {niche_id}: not eligible (tier below within_6_months OR compute failed)")
        return 0
    tier, subject, body_template, kit_url = tbb

    targets = _load_brand_targets(conn, niche_id)
    print(f"  {niche_id} (tier={tier}): {len(targets)} active brand targets")
    if not targets:
        return 0

    written = 0
    for t in targets:
        if _target_already_drafted_recent(conn, t["id"]):
            print(f"    skip {t['brand_name']}: within {_DEDUP_WINDOW_DAYS}d dedup window")
            continue
        body = _personalize(
            body_template, t["brand_name"], t["contact_first_name"], sender_name,
        )
        print(f"    {'[DRY]' if dry_run else 'DRAFT'} {t['brand_name']} <{t['brand_email']}> subject='{subject}'")
        if dry_run:
            continue
        if _persist_draft(conn, niche_id, t["id"], tier, subject, body, kit_url):
            written += 1
    if not dry_run and written > 0:
        conn.commit()
    return written


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
    # Operator's name from env. If unset, use "the Gen Lab team" as a
    # neutral fallback — operator will notice on first review.
    sender_name = os.environ.get(
        "GENLAB_OUTREACH_SENDER_NAME", "the Gen Lab team",
    )

    niches = (args.niche,) if args.niche else ACTIVE_NICHES

    import psycopg
    from psycopg.rows import dict_row

    total = 0
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        for niche_id in niches:
            total += _generate_niche(conn, niche_id, sender_name, args.dry_run)

    logger.info("outreach: wrote %d DRAFTED rows across %d niches",
                total, len(niches))
    print(f"\n  Total DRAFTED rows written: {total}")
    print("  Review pending drafts via GET /api/v1/sponsorship/pipeline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
