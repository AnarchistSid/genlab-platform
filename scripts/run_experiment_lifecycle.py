#!/usr/bin/env python3
"""Advance auto_experiments through the lifecycle: pending → running → completed.

Two passes per invocation:

1. **Start pending experiments.** Any pending row with ≥2 arms in its
   spec is advanced to ``running``. status flips to 'running' and
   started_at = NOW(). Called at the top of the pass so newly-parsed
   experiments start their measurement window immediately.

2. **Complete running experiments whose duration has elapsed.** For each
   row where NOW() > started_at + duration_days, compute per-arm reward
   from pending_feedback, write the result to auto_experiments.result,
   and mark status='completed'. Uses ``measure_experiment_result`` for
   the metric math.

Result JSON shape (see measure_experiment_result docstring):
    {
      "arm_rewards": {"<arm_id>": {"observed_reward": <f>, "n_samples": <int>}},
      "expected_metric_shift": <float>,
      "observed_lift": <float | null>,
      "met_threshold": <bool>,
      "sufficient_samples": <bool>,
      ...
    }

met_threshold=True → the strategist's testable_prediction was
confirmed. Operator sees this on the dashboard and can decide whether
to apply the corresponding arm_add (via the existing operator-review
path).

met_threshold=False → prediction NOT confirmed. Operator learns from
the failure without having to design and run the experiment manually.

Flag: GENLAB_AUTO_EXPERIMENT_ENABLED (strict-true).

Usage:
    python scripts/run_experiment_lifecycle.py         # dry-run
    python scripts/run_experiment_lifecycle.py --apply

Exit codes:
    0 — success (including nothing-to-do or flag-off)
    3 — unhandled exception (durable file written)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "genlab-core" / "src"))

logger = logging.getLogger("experiment_lifecycle")


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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="Actually mutate DB (default: dry-run)")
    ap.add_argument("--env-file", default="/opt/genlab/.env")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    _load_env(args.env_file)

    from genlab_core.scheduling.auto_experiment import (
        check_running_experiments,
        complete_experiment,
        is_enabled,
        list_experiments,
        measure_experiment_result,
        promote_verdict_to_proposal,
        start_pending_experiments,
    )

    if not is_enabled():
        logger.info(
            "GENLAB_AUTO_EXPERIMENT_ENABLED not set to 'true' — exiting cleanly"
        )
        return 0

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        logger.error("DATABASE_URL not set")
        return 2

    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        # Pass 1: start pending → running.
        if args.apply:
            started = start_pending_experiments(conn)
            if started:
                conn.commit()
                logger.info("[lifecycle] started %d pending experiments", started)
        else:
            # Dry-run: count how many WOULD start.
            pending = list_experiments(conn, status="pending", limit=100)
            ready_to_start = sum(
                1
                for e in pending
                if isinstance(e.get("spec"), dict)
                and len(e["spec"].get("arms", []) or []) >= 2
            )
            logger.info(
                "[lifecycle] would start %d/%d pending experiments",
                ready_to_start,
                len(pending),
            )

        # Pass 2: complete running experiments whose duration has elapsed.
        due = check_running_experiments(conn)
        logger.info("[lifecycle] %d running experiments due for completion", len(due))

        completed = 0
        met_threshold_count = 0
        insufficient_samples_count = 0
        promoted_count = 0

        for exp in due:
            exp_id = exp.get("id")
            niche_id = exp.get("niche_id")
            result = measure_experiment_result(conn, exp)

            met = result.get("met_threshold", False)
            suff = result.get("sufficient_samples", False)
            if met:
                met_threshold_count += 1
            if not suff:
                insufficient_samples_count += 1

            if args.apply:
                if complete_experiment(conn, exp_id, result):
                    completed += 1
                    logger.info(
                        "[lifecycle] completed exp=%s niche=%s met=%s suff=%s lift=%s",
                        (exp_id or "?")[:8],
                        niche_id,
                        met,
                        suff,
                        result.get("observed_lift"),
                    )
                    # Verdict-driven proposal promotion. Fail-open —
                    # the completion is already durable; a promotion
                    # failure logs a WARNING and continues.
                    promoted_arm, reason = promote_verdict_to_proposal(
                        conn, {**exp, "result": result}
                    )
                    if promoted_arm:
                        promoted_count += 1
                        logger.info(
                            "[lifecycle] promoted arm=%s exp=%s reason=%s",
                            promoted_arm,
                            (exp_id or "?")[:8],
                            reason,
                        )
                    else:
                        # DEBUG rather than WARNING — most experiments
                        # legitimately don't promote (verdict unmet,
                        # no matching proposal, low n).
                        logger.debug(
                            "[lifecycle] no promotion exp=%s reason=%s",
                            (exp_id or "?")[:8],
                            reason,
                        )
            else:
                print(
                    f"  DRY [{niche_id}] exp={exp_id[:8]} "
                    f"met_threshold={met} sufficient_samples={suff} "
                    f"observed_lift={result.get('observed_lift')}"
                )

        if args.apply:
            conn.commit()

        logger.info(
            "DONE completed=%d met_threshold=%d insufficient_samples=%d promoted=%d",
            completed,
            met_threshold_count,
            insufficient_samples_count,
            promoted_count,
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

            write_durable_error("run_experiment_lifecycle", exc)
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
