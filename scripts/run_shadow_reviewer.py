#!/usr/bin/env python3
"""Shadow reviewer CLI: run one pass over unshadowed scheduled blueprints.

Motivating problem: the AUTO #2 enforcement ratchet needs ≥30 fresh
calibration samples per niche + ≥90% agreement to widen enrollment.
Fresh samples come from ``calibration_logger.log()`` which fires only
on operator dashboard clicks. Nightly scheduler auto-approves 90% of
throughput → operator rarely clicks → ratchet stuck.

This script runs the LLM shadow reviewer over every VISUAL_READY +
scheduled blueprint that hasn't been shadow-reviewed yet, then writes
one calibration row per blueprint with ``source='shadow_reviewer'``.
Those rows populate the shadow-vs-gate confusion matrix (read via
``calibration_logger.stats(source_filter='shadow_reviewer')``) without
being counted as operator agreement — enrollment logic keeps its
existing safety.

Usage
=====

    # Dry run: show what would be shadowed, write nothing
    python scripts/run_shadow_reviewer.py

    # Apply for real (writes calibration rows)
    python scripts/run_shadow_reviewer.py --apply

    # Only a specific niche
    python scripts/run_shadow_reviewer.py --apply --niche gaming

    # Limit batch size for a canary run
    python scripts/run_shadow_reviewer.py --apply --limit 5

Exit codes
==========

* 0 — success (including nothing-to-do or feature-flag-off)
* 3 — unhandled exception (durable-file traceback written)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "genlab-core" / "src"))

logger = logging.getLogger("shadow_reviewer")


def _load_env(env_file: str = "/opt/genlab/.env") -> None:
    if os.environ.get("DATABASE_URL"):
        return
    env_path = Path(env_file)
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _fetch_unshadowed_blueprints(conn, niche_id, limit):
    """Fetch VISUAL_READY blueprints that haven't been shadow-reviewed."""
    where_niche = "AND b.niche_id = %s" if niche_id else ""
    params_niche = (niche_id,) if niche_id else ()

    sql = f"""
        SELECT b.id::text AS blueprint_id,
               b.niche_id,
               b.hook,
               b.title,
               b.extra
        FROM blueprints b
        WHERE b.status = 'VISUAL_READY'
          AND b.action_taken = 'approved'
          AND b.scheduled_for IS NOT NULL
          {where_niche}
          AND NOT EXISTS (
            SELECT 1 FROM auto_approval_calibration aac
            WHERE aac.blueprint_id = b.id::text
              AND aac.source = 'shadow_reviewer'
          )
        ORDER BY b.scheduled_for ASC
        LIMIT %s
    """
    return conn.execute(sql, params_niche + (limit,)).fetchall()


def _run_gate(blueprint) -> Any:
    """Evaluate the auto_approval_gate on the blueprint. Kept as a
    thin wrapper so tests can mock without touching the LLM SDK."""
    from genlab_core.scheduling.auto_approval_gate import evaluate

    return evaluate(blueprint)


def _write_calibration(
    blueprint_id: str,
    niche_id: str,
    gate_decision,
    shadow_verdict,
) -> bool:
    """Write one calibration row. Uses calibration_logger.log() so all
    the normalisation logic (feedback_category clamping, source
    normalisation, etc.) applies uniformly."""
    from genlab_core.scheduling.calibration_logger import log

    # Map the shadow verdict onto operator_action shape so the confusion
    # matrix compares like-for-like against the gate.
    operator_action = "approved" if shadow_verdict.would_approve else "rejected"
    return log(
        blueprint_id=blueprint_id,
        niche_id=niche_id,
        decision=gate_decision,
        operator_action=operator_action,
        feedback_category=(
            shadow_verdict.reason[:64] if not shadow_verdict.would_approve else None
        ),
        source="shadow_reviewer",
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="Write calibration rows (default: dry-run)")
    ap.add_argument("--niche", default=None, help="Restrict to one niche")
    ap.add_argument("--limit", type=int, default=100, help="Max blueprints per run")
    ap.add_argument("--env-file", default="/opt/genlab/.env")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    _load_env(args.env_file)

    from genlab_core.scheduling.shadow_reviewer import (
        evaluate_blueprint,
        is_enabled,
    )

    if not is_enabled():
        logger.info(
            "GENLAB_SHADOW_REVIEWER_ENABLED not set to 'true' — exiting cleanly"
        )
        return 0

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        logger.error("DATABASE_URL not set")
        return 2

    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        blueprints = _fetch_unshadowed_blueprints(
            conn, args.niche, args.limit
        )
        if not blueprints:
            logger.info("no unshadowed blueprints — exiting cleanly")
            return 0

        logger.info("found %d unshadowed blueprints", len(blueprints))
        if not args.apply:
            print(f"\nDRY RUN — would shadow {len(blueprints)} blueprints")
            for bp in blueprints[:10]:
                print(
                    f"  [{bp['niche_id']}] bp={bp['blueprint_id'][:8]} "
                    f"hook={str(bp.get('hook') or '')[:60]!r}"
                )
            return 0

        shadowed = 0
        skipped_error = 0
        shadow_approve = 0
        shadow_reject = 0
        # If the first blueprint hits a fatal LLM error (auth /
        # credit_exhausted), short-circuit the batch — burning through
        # 100 blueprints against a dead API just wastes retry cycles.
        FATAL_ERRORS = {"credit_exhausted", "auth", "connection"}

        for bp in blueprints:
            blueprint_dict = {
                "id": bp["blueprint_id"],
                "niche_id": bp["niche_id"],
                "status": "VISUAL_READY",
                "hook_text": bp.get("hook") or bp.get("title") or "",
                "extra": bp.get("extra") or {},
            }

            gate_decision = _run_gate(blueprint_dict)
            shadow = evaluate_blueprint(blueprint_dict)

            if shadow is None:
                logger.warning("shadow feature flag flipped off mid-run — stopping")
                break

            if shadow.is_error:
                skipped_error += 1
                logger.warning(
                    "[shadow] bp=%s error_reason=%s — skipping write",
                    bp["blueprint_id"][:8],
                    shadow.error_reason,
                )
                if shadow.error_reason in FATAL_ERRORS:
                    logger.warning(
                        "fatal LLM error (%s) — short-circuiting batch",
                        shadow.error_reason,
                    )
                    break
                continue

            ok = _write_calibration(
                blueprint_id=bp["blueprint_id"],
                niche_id=bp["niche_id"],
                gate_decision=gate_decision,
                shadow_verdict=shadow,
            )
            if ok:
                shadowed += 1
                if shadow.would_approve:
                    shadow_approve += 1
                else:
                    shadow_reject += 1

        logger.info(
            "DONE shadowed=%d shadow_approve=%d shadow_reject=%d skipped_error=%d",
            shadowed,
            shadow_approve,
            shadow_reject,
            skipped_error,
        )
        return 0


def _main_with_durable_error() -> int:
    try:
        return main()
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 0
    except Exception as exc:  # noqa: BLE001
        try:
            from genlab_core.observability.durable_error import write_durable_error

            write_durable_error("shadow_reviewer", exc)
        except Exception as import_exc:  # noqa: BLE001
            print(
                f"(also failed to import durable_error: {import_exc})",
                file=sys.stderr,
            )
            import traceback as _tb

            _tb.print_exc(file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(_main_with_durable_error())
