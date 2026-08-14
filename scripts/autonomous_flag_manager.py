#!/usr/bin/env python3
"""Phase 5.C session 1 — autonomous flag manager (proposer only).

Fires daily at 07:00 UTC via ``genlab-flag-manager.timer``. Runs
every registered flag-flip proposer, persists non-None proposals
to ``flag_flip_proposals`` in status='pending'.

Session 1 (this): PROPOSES only. Session 2 adds auto-apply after
24h operator-override window.

## Idempotency

Before persisting a new proposal for a flag, marks any prior
pending proposal for the same flag as ``superseded``. Only the
freshest proposal per flag stays pending — the operator sees a
clean queue, not a growing pile of stale suggestions.

## Usage

    uv run python scripts/autonomous_flag_manager.py
    uv run python scripts/autonomous_flag_manager.py --dry-run

## Exit codes

  * 0 — completed
  * 1 — DATABASE_URL unset
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

logger = logging.getLogger("autonomous_flag_manager")


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    return ap.parse_args(argv)


def _supersede_prior_pending(conn, flag_name: str) -> int:
    """Flip any existing pending row for this flag to 'superseded'.
    Returns count superseded. Fail-open to 0."""
    try:
        result = conn.execute(
            """
            UPDATE flag_flip_proposals
            SET status = 'superseded'
            WHERE flag_name = %s AND status = 'pending'
            """,
            (flag_name,),
        )
        return result.rowcount or 0
    except Exception as exc:
        logger.warning(
            "[flag_mgr] supersede failed flag=%s: %s", flag_name, exc,
        )
        try:
            conn.rollback()
        except Exception:
            pass
        return 0


def _persist_proposal(conn, proposal) -> bool:
    try:
        conn.execute(
            """
            INSERT INTO flag_flip_proposals
              (flag_name, from_state, to_state, rationale, evidence,
               confidence)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s)
            """,
            (
                proposal.flag_name, proposal.from_state,
                proposal.to_state, proposal.rationale,
                json.dumps(proposal.evidence), proposal.confidence,
            ),
        )
        return True
    except Exception as exc:
        logger.warning(
            "[flag_mgr] persist failed flag=%s: %s",
            proposal.flag_name, exc,
        )
        try:
            conn.rollback()
        except Exception:
            pass
        return False


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
    from genlab_core.scheduling.flag_flip_proposer import collect_proposals

    print("\nAutonomous flag manager (session 1: propose-only)")
    print(f"{'flag':50} {'from':>6} {'to':>6} {'conf':>6}")
    print("-" * 80)
    total_new = 0
    total_superseded = 0
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        proposals = collect_proposals(conn)
        if not proposals:
            print("  (no proposals today — evidence gates not met for any flag)")
            return 0
        for p in proposals:
            print(
                f"  {p.flag_name:50} {p.from_state:>6} → {p.to_state:>6} "
                f"conf={p.confidence:.2f}"
            )
            print(f"    {p.rationale}")
            if args.dry_run:
                continue
            total_superseded += _supersede_prior_pending(conn, p.flag_name)
            if _persist_proposal(conn, p):
                total_new += 1
        if not args.dry_run:
            conn.commit()

    logger.info(
        "[flag_mgr] proposals=%d superseded_prior=%d",
        total_new, total_superseded,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
