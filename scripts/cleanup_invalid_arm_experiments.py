#!/usr/bin/env python3
"""One-shot cleanup — mark running experiments whose arm_ids don't
exist in ``bandit_arms`` as ``discarded`` with reason=invalid_arms.

## Motivation

Phase 3.D session 2 dry-run (2026-08-14) surfaced that 30 of 42
running auto_experiments had zero samples per arm. Diagnostic query:

    74 unique arm_ids in auto_experiments.spec.arms
    19 unique arm_ids in pending_feedback (last 14d)
    12 overlap

The prior LLM parser prompt allowed the strategist to invent arm
names (metric labels like ``reward_binary_success``, compound
names like ``scene_reaction__tiktok_instagram``). Those invented
experiments could NEVER receive samples — they were dead on
arrival.

## What this script does

  1. Load per-niche real arm_ids from ``bandit_arms``.
  2. For each running experiment, check whether every arm in
     ``spec.arms`` exists in the real set for that niche.
  3. If any arm is invalid, mark ``discarded`` with
     ``result.reason='invalid_arms:<list>'`` so the strategist can
     see this specific failure mode (matches the Phase 3.D
     discarded convention).

## Safety

  * Read-only by default (``--dry-run``). Use ``--apply`` to write.
  * Prints every transition it would make.
  * Idempotent — running twice on the same DB is a no-op after
    the first apply.

## Usage

    uv run python scripts/cleanup_invalid_arm_experiments.py --dry-run
    uv run python scripts/cleanup_invalid_arm_experiments.py --apply

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
from datetime import UTC, datetime

logger = logging.getLogger("cleanup_invalid_arm_experiments")


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true",
                    help="Actually write (default: dry-run)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print transitions without writing (default)")
    return ap.parse_args(argv)


def _load_real_arms(conn) -> dict[str, frozenset[str]]:
    """Return {niche_id → frozenset(arm_ids)}."""
    rows = conn.execute(
        "SELECT niche_id, arm_id FROM bandit_arms"
    ).fetchall()
    by_niche: dict[str, set[str]] = {}
    for r in rows:
        niche = r.get("niche_id") if hasattr(r, "get") else r[0]
        arm = r.get("arm_id") if hasattr(r, "get") else r[1]
        by_niche.setdefault(niche, set()).add(arm)
    return {n: frozenset(v) for n, v in by_niche.items()}


def _load_running_experiments(conn):
    return conn.execute(
        """
        SELECT id::text AS id, niche_id, spec
        FROM auto_experiments
        WHERE status = 'running'
        ORDER BY started_at ASC
        """
    ).fetchall()


def _extract_arms(spec) -> list[str]:
    if isinstance(spec, str):
        try:
            spec = json.loads(spec)
        except json.JSONDecodeError:
            return []
    if not isinstance(spec, dict):
        return []
    arms = spec.get("arms") or []
    if not isinstance(arms, list):
        return []
    return [str(a).strip() for a in arms if str(a).strip()]


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

    apply_mode = args.apply
    if apply_mode and args.dry_run:
        logger.error("--apply and --dry-run are mutually exclusive")
        return 1

    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        real_arms = _load_real_arms(conn)
        experiments = _load_running_experiments(conn)

        total = len(experiments)
        invalid_count = 0
        valid_count = 0

        print(f"\nScanning {total} running experiments "
              f"(mode={'APPLY' if apply_mode else 'DRY-RUN'}):\n")

        for exp in experiments:
            exp_id = exp["id"]
            niche = exp["niche_id"]
            arms = _extract_arms(exp["spec"])
            real = real_arms.get(niche, frozenset())
            invalid = [a for a in arms if a not in real]

            if not invalid:
                valid_count += 1
                continue

            invalid_count += 1
            print(f"  {exp_id[:8]} {niche:12} arms={arms} invalid={invalid}")

            if apply_mode:
                result = {
                    "verdict": "DISCARDED",
                    "reason": (
                        f"invalid_arms:{','.join(invalid)[:200]} — "
                        "arms not in bandit_arms for this niche; strategist "
                        "invented names not corresponding to real arms"
                    ),
                    "cleanup_at": datetime.now(UTC).isoformat(),
                    "cleanup_by": "cleanup_invalid_arm_experiments.py",
                }
                try:
                    conn.execute(
                        """
                        UPDATE auto_experiments
                        SET status = 'discarded',
                            completed_at = NOW(),
                            result = %s::jsonb
                        WHERE id = %s AND status = 'running'
                        """,
                        (json.dumps(result), exp_id),
                    )
                except Exception as exc:
                    logger.warning(
                        "[cleanup] persist failed %s: %s", exp_id, exc,
                    )

        if apply_mode:
            conn.commit()

    print(f"\nSummary:")
    print(f"  Total running scanned: {total}")
    print(f"  Valid (all arms exist): {valid_count}")
    print(f"  Invalid → {'discarded' if apply_mode else 'would discard'}: {invalid_count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
