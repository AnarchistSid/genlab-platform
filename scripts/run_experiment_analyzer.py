#!/usr/bin/env python3
"""Phase 3.D session 2 — experiment analyzer runner.

Fires every 6h via ``genlab-experiment-analyzer.timer``. For each
RUNNING experiment in ``auto_experiments``:

  1. Query raw reward_48h samples per arm from ``pending_feedback``
     within the [started_at, now()] window.
  2. Run ``compute_verdict`` from
     ``genlab_core.scheduling.experiment_analysis``.
  3. Take action based on verdict:
       - B_WINS / A_WINS → mark completed (with winning-arm annotation).
       - NO_SIGNAL + duration exceeded → mark completed as null-result.
       - INSUFFICIENT_SAMPLES + duration exceeded → mark discarded
         (roadmap: "Zero ran-for-2-weeks-no-signal experiments").
       - Otherwise (still running, still gathering) → no-op.

## Design decisions

* **Read-only in v1** (dry-run when flag off). Session 3 will
  optionally auto-apply the winning arm's config change via
  strategist. Until then, the operator reads completed experiments
  and manually applies the change.
* **Early-stop is enabled by default** — waiting to full duration
  when we already have B_WINS with p=0.99 wastes traffic. Set
  ``GENLAB_EXPERIMENT_EARLY_STOP=0`` to disable.
* **Futility-stop** — the roadmap's success criterion is "zero
  wasted 2-week experiments". If duration exceeded AND still
  INSUFFICIENT_SAMPLES, mark as ``discarded`` (not ``completed``)
  so the strategist can see this was a null result AND
  distinguish "we tested and it didn't work" from "we tested and
  didn't get enough traffic to know".

## Usage

    uv run python scripts/run_experiment_analyzer.py
    uv run python scripts/run_experiment_analyzer.py --dry-run
    uv run python scripts/run_experiment_analyzer.py --experiment-id <uuid>

## Exit codes

  * 0 — completed (any number of transitions)
  * 1 — DATABASE_URL unset
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime

logger = logging.getLogger("run_experiment_analyzer")


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--experiment-id", default=None,
                    help="Analyze one specific experiment (skip status filter)")
    ap.add_argument("--early-stop",
                    action=argparse.BooleanOptionalAction,
                    default=None,
                    help="Override GENLAB_EXPERIMENT_EARLY_STOP env")
    return ap.parse_args(argv)


def _early_stop_enabled(cli_override) -> bool:
    if cli_override is not None:
        return bool(cli_override)
    return os.environ.get(
        "GENLAB_EXPERIMENT_EARLY_STOP", "1",
    ).strip().lower() in {"1", "true", "yes"}


def _load_running_experiments(conn, experiment_id=None):
    """Return running experiments (or one specific one). Fail-open
    to empty list."""
    try:
        if experiment_id:
            rows = conn.execute(
                """
                SELECT id::text AS id, niche_id, spec, started_at,
                       EXTRACT(EPOCH FROM (NOW() - started_at))::int AS age_seconds
                FROM auto_experiments
                WHERE id = %s
                """,
                (experiment_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id::text AS id, niche_id, spec, started_at,
                       EXTRACT(EPOCH FROM (NOW() - started_at))::int AS age_seconds
                FROM auto_experiments
                WHERE status = 'running'
                  AND started_at IS NOT NULL
                ORDER BY started_at ASC
                """
            ).fetchall()
    except Exception as exc:
        logger.warning("[analyzer] load failed: %s", exc)
        return []
    out = []
    for r in rows or []:
        spec = r.get("spec") if hasattr(r, "get") else r[2]
        if isinstance(spec, str):
            try:
                spec = json.loads(spec)
            except json.JSONDecodeError:
                spec = {}
        out.append({
            "id": r.get("id") if hasattr(r, "get") else r[0],
            "niche_id": r.get("niche_id") if hasattr(r, "get") else r[1],
            "spec": spec or {},
            "started_at": r.get("started_at") if hasattr(r, "get") else r[3],
            "age_seconds": int(r.get("age_seconds") if hasattr(r, "get") else r[4] or 0),
        })
    return out


def _fetch_arm_samples(conn, niche_id: str, arm_id: str,
                       started_at) -> list[float]:
    """Return raw reward_48h floats for one arm since experiment start.
    Fail-open to empty list."""
    try:
        rows = conn.execute(
            """
            SELECT reward_48h::float AS r
            FROM pending_feedback
            WHERE niche_id = %s
              AND arm_id = %s
              AND reward_48h IS NOT NULL
              AND updated_at >= %s
            ORDER BY updated_at ASC
            """,
            (niche_id, arm_id, started_at),
        ).fetchall()
    except Exception as exc:
        logger.warning(
            "[analyzer] sample fetch failed niche=%s arm=%s: %s",
            niche_id, arm_id, exc,
        )
        return []
    return [
        float(r.get("r") if hasattr(r, "get") else r[0])
        for r in rows or []
        if (r.get("r") if hasattr(r, "get") else r[0]) is not None
    ]


def _finalize(conn, experiment_id: str, status: str, result: dict) -> bool:
    """Move experiment to terminal state with result JSONB."""
    try:
        conn.execute(
            """
            UPDATE auto_experiments
            SET status = %s,
                completed_at = NOW(),
                result = %s::jsonb
            WHERE id = %s AND status = 'running'
            """,
            (status, json.dumps(result), experiment_id),
        )
        return True
    except Exception as exc:
        logger.warning("[analyzer] finalize %s failed: %s", experiment_id, exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def _analyze_one(conn, experiment: dict, early_stop: bool,
                 dry_run: bool) -> str:
    """Analyze one running experiment. Returns action label for the
    summary line: 'B_WINS' / 'A_WINS' / 'NO_SIGNAL' / 'DISCARDED' /
    'HOLD' / 'SKIP:bad_spec'."""
    from genlab_core.scheduling.experiment_analysis import (
        ArmObservations,
        ExperimentVerdict,
        compute_verdict,
    )

    spec = experiment["spec"]
    arms = spec.get("arms") or []
    duration_days = int(spec.get("duration_days") or 7)
    baseline = float(spec.get("baseline_reward") or 0.5)
    age_days = experiment["age_seconds"] / 86400.0
    duration_exceeded = age_days >= duration_days

    if len(arms) != 2:
        logger.warning(
            "[analyzer] experiment %s: expected 2 arms got %d — skipping",
            experiment["id"], len(arms),
        )
        return "SKIP:bad_spec"

    arm_a_samples = _fetch_arm_samples(
        conn, experiment["niche_id"], str(arms[0]),
        experiment["started_at"],
    )
    arm_b_samples = _fetch_arm_samples(
        conn, experiment["niche_id"], str(arms[1]),
        experiment["started_at"],
    )

    verdict_result = compute_verdict(
        ArmObservations(arm_id=str(arms[0]), rewards=tuple(arm_a_samples)),
        ArmObservations(arm_id=str(arms[1]), rewards=tuple(arm_b_samples)),
        baseline=baseline,
    )
    v = verdict_result.verdict

    # Decision matrix — matches the docstring
    should_finalize = False
    finalize_status = "completed"
    if v == ExperimentVerdict.B_WINS or v == ExperimentVerdict.A_WINS:
        # Winning verdict — always finalize (early-stop honored).
        # If early_stop=false, only finalize when duration_exceeded.
        if early_stop or duration_exceeded:
            should_finalize = True
    elif v == ExperimentVerdict.NO_SIGNAL and duration_exceeded:
        should_finalize = True  # completed with null result
    elif v == ExperimentVerdict.INSUFFICIENT_SAMPLES and duration_exceeded:
        # Futility-stop — mark discarded so strategist sees this was a
        # traffic-insufficient run, not a completed test.
        should_finalize = True
        finalize_status = "discarded"

    logger.info(
        "[analyzer] exp=%s niche=%s arms=%s verdict=%s p_b_beats_a=%.3f "
        "n_a=%d n_b=%d age=%.1fd duration=%dd finalize=%s status=%s",
        experiment["id"][:8], experiment["niche_id"], arms,
        v.value, verdict_result.prob_b_beats_a,
        verdict_result.n_a, verdict_result.n_b,
        age_days, duration_days,
        should_finalize, finalize_status if should_finalize else "held",
    )

    if not should_finalize:
        return "HOLD"

    if dry_run:
        return f"DRY:{finalize_status}:{v.value}"

    result = {
        "verdict": v.value,
        "prob_b_beats_a": verdict_result.prob_b_beats_a,
        "posterior_a_mean": verdict_result.posterior_a_mean,
        "posterior_b_mean": verdict_result.posterior_b_mean,
        "n_a": verdict_result.n_a,
        "n_b": verdict_result.n_b,
        "arms": [str(arms[0]), str(arms[1])],
        "reason": verdict_result.reason,
        "analyzed_at": datetime.now(UTC).isoformat(),
        "early_stop_used": (not duration_exceeded and should_finalize),
    }
    if _finalize(conn, experiment["id"], finalize_status, result):
        conn.commit()
        return "DISCARDED" if finalize_status == "discarded" else v.value
    return "PERSIST_FAILED"


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

    early_stop = _early_stop_enabled(args.early_stop)

    import psycopg
    from psycopg.rows import dict_row

    counts: dict[str, int] = {}
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        experiments = _load_running_experiments(conn, args.experiment_id)
        if not experiments:
            logger.info("[analyzer] no running experiments — nothing to do")
            return 0
        print(f"\nAnalyzing {len(experiments)} running experiment(s) "
              f"(early_stop={early_stop}, dry_run={args.dry_run}):")
        for exp in experiments:
            action = _analyze_one(conn, exp, early_stop, args.dry_run)
            counts[action] = counts.get(action, 0) + 1

    print("\nSummary:")
    for action, n in sorted(counts.items()):
        print(f"  {action}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
