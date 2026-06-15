#!/usr/bin/env python3
"""Backfill auto_approval_calibration from dashboard_events.

AUTO #2 Day-1 step D1.2 per docs/runbooks/AUTO_2_ROLLOUT_2026-06-15.md.

## Why

Before AUTO #2 enforcement flips on, we need calibration data
(gate-verdict + operator-action pairs) to justify the threshold +
prove the gate's decisions match what operators actually do.

calibration_logger.log() started writing rows on 2026-06-13 (AUTO #1
trilogy), but bulk-review and other paths bypass it (4 paths total per
Round-3 audit). dashboard_events HAS the full operator review history
because PR #206 wired _execute_review_action to emit those events.

This script reads operator review events from dashboard_events, runs
each blueprint through the auto_approval_gate, and writes a calibration
row with the HISTORICAL decided_at preserved (via PR #221's
calibration_logger decided_at kwarg).

## What it does

1. Read dashboard_events where event_type IN (review_approved,
   review_rejected, review_revised, review_skipped).
2. For each row: filter out non-UUID entity_id (synthetic test rows),
   fetch the blueprint from blueprints, build the gate's extra
   wrapper (mirrors PR #221's auto_approver fix), run the gate,
   write calibration_logger.log(decided_at=event.created_at).
3. Idempotent — checks (blueprint_id, decided_at) tuple before writing.

## Defensive design

- Dry-run by default; --apply to execute.
- Per-row failures don't stop the batch (logged + counted).
- Blueprint-not-found (archived/deleted) → skip with reason.
- Synthetic entity_ids (length < 36) → skip.
- Decided_at is taken from dashboard_events.created_at (the operator
  click time), NOT NOW() — preserves the temporal calibration data.
- Re-runs are safe — the (blueprint_id, decided_at) idempotency check
  via a count query means a re-run on already-backfilled data writes
  zero rows.

## Pre-merge checks (5 mandatory per runbook § 2.2)

1. ✓ Dry-run flag exists and prints intent without writing
2. ✓ Idempotency check via per-row SELECT before INSERT
3. ✓ decided_at sourced from dashboard_events.created_at (NOT NOW())
4. ✓ Real gate evaluation per blueprint (not synthetic verdict)
5. ✓ event_type → operator_action mapping documented in code

## Operator usage (prod Hetzner box)

    cd /opt/genlab
    PYTHONPATH=genlab-core/src python3 scripts/backfill_calibration_2026_06_15.py
    PYTHONPATH=genlab-core/src python3 scripts/backfill_calibration_2026_06_15.py --apply
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# Map dashboard_events.event_type → calibration_logger operator_action.
# review_approved → approved (operator clicked Approve)
# review_rejected → rejected (operator clicked Reject)
# review_revised  → revised  (operator clicked Revise)
# review_skipped  → skipped  (operator clicked Skip)
EVENT_TYPE_TO_OPERATOR_ACTION: dict[str, str] = {
    "review_approved": "approved",
    "review_rejected": "rejected",
    "review_revised": "revised",
    "review_skipped": "skipped",
}


def _is_uuid_format(s: str) -> bool:
    """Real blueprint_ids are 36-char UUIDs; synthetic test ids are short
    integer strings ('10001', '12345', etc.). Match D1.1's filter so the
    two scripts never disagree on what's synthetic."""
    return isinstance(s, str) and len(s) >= 36


def _build_gate_extra_wrapper(blueprint_dict: dict) -> dict:
    """Mirror the wrapper construction in:
    - dashboard/server/api/blueprints.py::auto_approval_preview
    - genlab-core/.../scheduling/auto_approver.py (PR #221)

    Three sites MUST stay aligned — if the gate adds a new field, all
    three need to expose it. Without the wrapper, the gate sees None
    for every score and aggregates confidence to ~0.5 regardless of
    blueprint quality (Showstopper #1, 2026-06-15).
    """
    if isinstance(blueprint_dict.get("extra"), dict):
        return blueprint_dict["extra"]

    raw_visual = blueprint_dict.get("visual_paths")
    if isinstance(raw_visual, list):
        visual_paths = raw_visual
    elif isinstance(raw_visual, str) and raw_visual.strip():
        try:
            decoded = json.loads(raw_visual)
            visual_paths = decoded if isinstance(decoded, list) else []
        except (ValueError, TypeError):
            visual_paths = []
    else:
        visual_paths = []

    return {
        "visual_paths": visual_paths,
        "composite_score": blueprint_dict.get("composite_score"),
        "virality_score": blueprint_dict.get("virality_score"),
        "validation_status": blueprint_dict.get("validation_status"),
    }


def _calibration_row_exists(cur, blueprint_id: str, decided_at: datetime) -> bool:
    """Idempotency check — exact (blueprint_id, decided_at) tuple lookup.
    Returns True if a row already exists for this operator click."""
    cur.execute(
        """
        SELECT 1 FROM auto_approval_calibration
        WHERE blueprint_id = %s AND decided_at = %s
        LIMIT 1
        """,
        (blueprint_id, decided_at),
    )
    return cur.fetchone() is not None


def _fetch_review_events(cur) -> list[dict[str, Any]]:
    """Pull every review-action event in chronological order."""
    cur.execute(
        """
        SELECT id, event_type, entity_id, niche_id, created_at
        FROM dashboard_events
        WHERE event_type IN ('review_approved', 'review_rejected',
                             'review_revised', 'review_skipped')
        ORDER BY created_at ASC
        """
    )
    return [
        {
            "event_id": row[0],
            "event_type": row[1],
            "blueprint_id": row[2],
            "niche_id": row[3],
            "decided_at": row[4],
        }
        for row in cur.fetchall()
    ]


def _fetch_blueprint(cur, blueprint_id: str) -> dict | None:
    """Fetch a blueprint by id. Returns None if not found (archived/
    deleted in between operator click and backfill run).

    The score fields (composite_score/virality_score/validation_status/
    visual_paths) live in the ``extra`` JSONB column on this table —
    NOT as promoted columns. The wrapper builder handles both layouts
    (it short-circuits when ``extra`` is already a dict)."""
    cur.execute(
        """
        SELECT id, niche_id, status, hook_text, title, extra
        FROM blueprints WHERE id = %s
        """,
        (blueprint_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {
        "id": str(row[0]),
        "niche_id": row[1] or "",
        "status": row[2],
        "hook_text": row[3] or "",
        "title": row[4] or "",
        "extra": row[5],
    }


def _process_one_event(cur, event: dict, *, dry_run: bool) -> str:
    """Process a single dashboard_events row. Returns one of:
    'written', 'dryrun', 'skip_synthetic', 'skip_bp_missing',
    'skip_niche_missing', 'skip_already_logged', 'skip_unknown_event',
    'skip_gate_error'."""
    blueprint_id = event["blueprint_id"] or ""
    if not _is_uuid_format(blueprint_id):
        return "skip_synthetic"

    operator_action = EVENT_TYPE_TO_OPERATOR_ACTION.get(event["event_type"])
    if operator_action is None:
        return "skip_unknown_event"

    niche_id = event["niche_id"] or ""
    if not niche_id:
        # Some old events have empty niche_id — backfill needs it.
        return "skip_niche_missing"

    # Idempotency: already-backfilled rows must not be re-written.
    if _calibration_row_exists(cur, blueprint_id, event["decided_at"]):
        return "skip_already_logged"

    blueprint = _fetch_blueprint(cur, blueprint_id)
    if blueprint is None:
        return "skip_bp_missing"

    # Build the gate's extra wrapper + evaluate.
    from genlab_core.scheduling.auto_approval_gate import evaluate as gate_evaluate

    blueprint["extra"] = _build_gate_extra_wrapper(blueprint)
    try:
        decision = gate_evaluate(blueprint)
    except Exception as exc:
        logger.warning(
            "[backfill] gate evaluation failed for bp=%s: %s", blueprint_id, exc
        )
        return "skip_gate_error"

    if dry_run:
        logger.info(
            "[backfill][DRY] bp=%s niche=%s gate_approved=%s gate_conf=%.3f op=%s decided_at=%s",
            blueprint_id,
            niche_id,
            decision.approved,
            decision.confidence,
            operator_action,
            event["decided_at"].isoformat(),
        )
        return "dryrun"

    from genlab_core.scheduling.calibration_logger import log as calibration_log

    success = calibration_log(
        blueprint_id=blueprint_id,
        niche_id=niche_id,
        decision=decision,
        operator_action=operator_action,
        decided_at=event["decided_at"],
    )
    return "written" if success else "skip_gate_error"


def run(dsn: str, *, dry_run: bool) -> dict[str, int]:
    """Execute the backfill. Returns per-outcome counts."""
    import psycopg

    counts: dict[str, int] = {}
    with psycopg.connect(dsn, connect_timeout=10) as conn:
        # Use a read cursor for events + blueprints; calibration_logger
        # opens its own connection for the writes. Keeping reads and
        # writes on separate connections lets calibration_logger's
        # error path (fail-open) work as designed.
        with conn.cursor() as cur:
            events = _fetch_review_events(cur)
            logger.info("[backfill] found %d review events to consider", len(events))
            for event in events:
                outcome = _process_one_event(cur, event, dry_run=dry_run)
                counts[outcome] = counts.get(outcome, 0) + 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill auto_approval_calibration from dashboard_events"
    )
    parser.add_argument(
        "--apply", action="store_true", help="Actually write rows (default: dry-run)"
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        logger.error("DATABASE_URL not set — cannot backfill")
        return 1

    counts = run(dsn, dry_run=not args.apply)

    print()
    print("─" * 70)
    print(" Backfill outcomes")
    print("─" * 70)
    for outcome in (
        "written",
        "dryrun",
        "skip_synthetic",
        "skip_bp_missing",
        "skip_niche_missing",
        "skip_already_logged",
        "skip_unknown_event",
        "skip_gate_error",
    ):
        if outcome in counts:
            print(f"  {outcome:<22} {counts[outcome]}")
    print()
    if not args.apply:
        print("DRY-RUN — no changes made. Re-run with --apply to write rows.")
    else:
        print("Backfill complete. Re-running is safe (idempotency check).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
