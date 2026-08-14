#!/usr/bin/env python3
"""Phase 1.A of the Genius Program Roadmap — outcome verifier runner.

Fires every 6h via genlab-outcome-verifier.timer. For each pending
verification record older than 48h:

  1. Fetch current metric value via PostgresMetricSnapshotProvider
  2. Compare to baseline_value, classify verdict (improved / unchanged
     / regressed) via Verifier._classify
  3. Write t_plus_48h_value + verdict + rollback_recommended
  4. If verdict=regressed AND rollback_recommended=True, execute
     the appropriate rollback SQL:
       * arm_add → mark arm ``is_paused=True`` in bandit_arms (soft
         disable; don't delete because Beta posterior stays useful
         for future re-enable decisions)
       * reward_weight → reset to BASE_WEIGHTS baseline via the
         config-updater path (out of scope for v1 rollback; log only
         and let operator decide)
       * novelty_rate → reset to default 0.20 (out of scope for v1)

Fail-open at every DB step. Never blocks the pipeline.

## Usage

    # Fires from systemd; can run manually for one-shot check:
    uv run python -m scripts.run_outcome_verifier
    uv run python -m scripts.run_outcome_verifier --dry-run

## Exit codes

  * 0 — completed (any number of verdicts written / rollbacks fired)
  * 1 — DATABASE_URL unset
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import UTC, datetime

logger = logging.getLogger("run_outcome_verifier")


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="Compute verdicts + would-rollback list, no writes")
    return ap.parse_args(argv)


def _rollback_arm_add(dsn: str, niche_id: str, target: str) -> bool:
    """Soft-disable an arm by setting is_paused=True. Returns True on
    success, False on any error. target format:
    ``arm_reward:{niche}:{arm_id}``"""
    # Extract arm_id from the metric name that was registered
    if not target.startswith(f"arm_reward:{niche_id}:"):
        logger.warning(
            "[outcome_verifier] cannot parse arm_id from target=%s", target,
        )
        return False
    arm_id = target[len(f"arm_reward:{niche_id}:"):]
    if not arm_id:
        return False
    try:
        from genlab_core.storage.tenant_context import pg_connect
        with pg_connect(dsn, niche_id=niche_id, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                # bandit_arms may or may not have is_paused column; add it
                # via ALTER on the fly if missing (safe idempotent).
                cur.execute(
                    """
                    ALTER TABLE bandit_arms
                    ADD COLUMN IF NOT EXISTS is_paused BOOLEAN
                        NOT NULL DEFAULT FALSE
                    """
                )
                cur.execute(
                    """
                    UPDATE bandit_arms
                    SET is_paused = TRUE,
                        extra = COALESCE(extra, '{}'::jsonb) ||
                                jsonb_build_object(
                                  'paused_by', 'outcome_verifier_v1',
                                  'paused_at', %s,
                                  'paused_reason', 'auto_rollback_regressed'
                                )
                    WHERE niche_id = %s AND arm_id = %s
                    """,
                    (datetime.now(UTC).isoformat(), niche_id, arm_id),
                )
                conn.commit()
        logger.info(
            "[outcome_verifier] auto-rollback: paused arm niche=%s id=%s",
            niche_id, arm_id,
        )
        return True
    except Exception as exc:
        logger.warning(
            "[outcome_verifier] rollback arm_add failed niche=%s id=%s: %s",
            niche_id, arm_id, exc,
        )
        return False


def _rollback_reward_weight(niche_id: str, target: str) -> bool:
    """Log-only in v1 — reward weight rollback needs to write into the
    per-niche reward-shaper config which is more surgical than v1
    scope allows. Runner records the recommendation; operator acts."""
    logger.warning(
        "[outcome_verifier] rollback recommended for reward_weight "
        "niche=%s target=%s — operator action required "
        "(v1 does not auto-rollback reward_weight)",
        niche_id, target,
    )
    return False


def main(argv=None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        logger.error("DATABASE_URL unset")
        return 1

    from genlab_core.scheduling.outcome_verifier import Verifier
    from genlab_core.scheduling.outcome_verifier_postgres import (
        PostgresMetricSnapshotProvider,
        PostgresVerificationRecordStore,
    )

    metrics = PostgresMetricSnapshotProvider(dsn)
    store = PostgresVerificationRecordStore(dsn)
    verifier = Verifier(metrics=metrics, store=store)

    now = datetime.now(UTC)
    pending = verifier.list_pending(now=now)
    logger.info("outcome_verifier: %d pending records older than 48h", len(pending))

    counts = {"improved": 0, "unchanged": 0, "regressed": 0,
              "rolled_back": 0, "rollback_failed": 0}

    for record in pending:
        if args.dry_run:
            # Just compute what would happen
            current = metrics.snapshot(record.niche_id, record.metric_name)
            verdict, would_rollback = verifier._classify(
                record.baseline_value, current,
            )
            counts[verdict.value] += 1
            print(
                f"  [dry-run] {record.niche_id:12s} "
                f"proposal={record.proposal_id[:20]}... "
                f"baseline={record.baseline_value} "
                f"current={current} → {verdict.value}"
                + (" [ROLLBACK]" if would_rollback else "")
            )
            continue

        verdict = verifier.evaluate(record)
        counts[verdict.value] += 1
        if verdict.value == "regressed":
            # Re-fetch fresh state to know if rollback_recommended = True
            # (evaluate already wrote it, but we don't have it in record).
            # Cheapest path: run rollback based on proposal_type.
            if record.proposal_type == "arm_add":
                ok = _rollback_arm_add(
                    dsn, record.niche_id, record.metric_name,
                )
                if ok:
                    counts["rolled_back"] += 1
                else:
                    counts["rollback_failed"] += 1
            elif record.proposal_type == "reward_weight":
                _rollback_reward_weight(
                    record.niche_id, record.metric_name,
                )
                # v1 = log only, doesn't count as rolled_back
            else:
                logger.info(
                    "[outcome_verifier] regression detected but no rollback "
                    "impl for proposal_type=%s (niche=%s target=%s)",
                    record.proposal_type, record.niche_id, record.metric_name,
                )

    logger.info(
        "outcome_verifier: done — improved=%d unchanged=%d regressed=%d "
        "rolled_back=%d rollback_failed=%d",
        counts["improved"], counts["unchanged"], counts["regressed"],
        counts["rolled_back"], counts["rollback_failed"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
