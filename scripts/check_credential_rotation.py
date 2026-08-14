#!/usr/bin/env python3
"""Phase 2.E — credential rotation status.

Reports which secrets are overdue vs recently rotated. Operator-
runnable; does NOT auto-rotate (per-service surgery deferred).

## Credential inventory

Hardcoded list of secrets we track. Each entry knows its rotation
interval + service label. `--seed` inserts unseen entries into
the DB with `last_rotated_at=NULL, next_rotation_due_at=NOW()`
so they surface as overdue immediately.

## Usage

    # Report status (dry-run default)
    uv run python scripts/check_credential_rotation.py

    # Seed unseen entries into DB
    uv run python scripts/check_credential_rotation.py --seed

    # Mark a credential as just-rotated (operator did the rotation)
    uv run python scripts/check_credential_rotation.py \\
      --mark-rotated META_ACCESS_TOKEN

## Related

  * Rule #33 (SaaS blocker) — genlab role has BYPASSRLS; rotating
    to a non-bypass role is the top-priority rotation.
  * .audit/RUNBOOK_credential_rotation.md — per-service how-to
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import UTC, datetime, timedelta

logger = logging.getLogger("check_credential_rotation")

# Credential inventory. Each entry: (name, service, rotation_interval_days)
INVENTORY = [
    ("DATABASE_URL", "postgres", 180),
    ("POSTGRES_PASSWORD", "postgres", 90),
    ("MIGRATION_DATABASE_URL", "postgres", 180),
    ("ANTHROPIC_API_KEY", "anthropic", 90),
    ("OPENAI_API_KEY", "openai", 90),
    ("YOUTUBE_API_KEY", "youtube", 180),
    ("META_ACCESS_TOKEN", "meta", 365),  # EAA page tokens are effectively permanent
    ("THREADS_ACCESS_TOKEN", "meta", 90),
    ("YT_DLP_COOKIES_FILE", "youtube", 30),
    ("REVIEW_AUTH_PASS", "dashboard", 90),
    ("ELEVENLABS_API_KEY", "elevenlabs", 180),
    ("PEXELS_API_KEY", "pexels", 365),
    ("SENDGRID_API_KEY", "sendgrid", 180),
]


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seed", action="store_true",
                    help="Insert unseen credentials into the state table")
    ap.add_argument("--mark-rotated", default=None,
                    help="Mark credential as just-rotated (updates last/next)")
    return ap.parse_args(argv)


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

    now = datetime.now(UTC)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        # Mark-rotated shortcut
        if args.mark_rotated:
            entry = next(
                (e for e in INVENTORY if e[0] == args.mark_rotated), None,
            )
            if not entry:
                print(f"ERROR: {args.mark_rotated} not in inventory")
                return 1
            _, _, interval = entry
            conn.execute(
                """
                INSERT INTO credential_rotation_state
                  (credential_name, service, rotation_interval_days,
                   last_rotated_at, next_rotation_due_at, rotation_source)
                VALUES (%s, %s, %s, %s, %s, 'operator')
                ON CONFLICT (credential_name) DO UPDATE SET
                  last_rotated_at = EXCLUDED.last_rotated_at,
                  next_rotation_due_at = EXCLUDED.next_rotation_due_at,
                  rotation_source = 'operator',
                  updated_at = NOW()
                """,
                (
                    args.mark_rotated, entry[1], interval,
                    now, now + timedelta(days=interval),
                ),
            )
            conn.commit()
            print(f"Marked {args.mark_rotated} rotated. "
                  f"Next due {now + timedelta(days=interval):%Y-%m-%d}.")
            return 0

        # Seed unseen entries
        if args.seed:
            for name, service, interval in INVENTORY:
                conn.execute(
                    """
                    INSERT INTO credential_rotation_state
                      (credential_name, service, rotation_interval_days,
                       next_rotation_due_at, rotation_source)
                    VALUES (%s, %s, %s, %s, 'seed')
                    ON CONFLICT (credential_name) DO NOTHING
                    """,
                    (name, service, interval, now),
                )
            conn.commit()

        # Report status
        rows = conn.execute(
            """
            SELECT credential_name, service, rotation_interval_days,
                   last_rotated_at, next_rotation_due_at, rotation_source
            FROM credential_rotation_state
            ORDER BY next_rotation_due_at ASC NULLS FIRST
            """,
        ).fetchall()

        print()
        print(f"{'credential':30} {'service':12} {'last_rotated':12} "
              f"{'next_due':12} {'status'}")
        print("-" * 90)
        for r in rows:
            last = (
                r["last_rotated_at"].strftime("%Y-%m-%d")
                if r["last_rotated_at"] else "never"
            )
            due = (
                r["next_rotation_due_at"].strftime("%Y-%m-%d")
                if r["next_rotation_due_at"] else "unknown"
            )
            if r["next_rotation_due_at"] is None:
                status = "no schedule"
            elif r["next_rotation_due_at"] < now:
                overdue_days = (now - r["next_rotation_due_at"]).days
                status = f"OVERDUE {overdue_days}d"
            elif r["next_rotation_due_at"] < now + timedelta(days=14):
                due_days = (r["next_rotation_due_at"] - now).days
                status = f"due in {due_days}d"
            else:
                status = "ok"
            print(f"{r['credential_name']:30} {r['service']:12} "
                  f"{last:12} {due:12} {status}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
