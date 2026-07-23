#!/usr/bin/env python3
"""Parse strategist testable_predictions → auto_experiments queue.

Reads unreviewed strategist_reports, filters medium/high-confidence
causal_hypotheses, runs Claude Haiku to parse the testable_prediction
field into an ExperimentSpec, then queues the spec into
auto_experiments via queue_pending_experiment.

Idempotency: queue_pending_experiment has ON CONFLICT DO NOTHING on
(source_report_id, hypothesis_index). Re-running produces zero
writes for already-queued entries.

Fatal-error short-circuit: if the LLM parser hits
credit_exhausted / auth / connection on the first few hypotheses,
the batch aborts — matches shadow_reviewer.py's short-circuit pattern
so we don't hammer a dead API.

Usage:
    python scripts/parse_testable_predictions.py           # dry-run
    python scripts/parse_testable_predictions.py --apply
    python scripts/parse_testable_predictions.py --apply --niche gaming
    python scripts/parse_testable_predictions.py --apply --limit 5

Exit codes:
    0 — success
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

logger = logging.getLogger("parse_testable_predictions")


# Errors that mean "the LLM is unreachable" — batch aborts on first hit.
_FATAL_LLM_ERRORS = frozenset(
    {"credit_exhausted", "auth", "connection", "not_configured", "sdk_missing"}
)


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


def _fetch_reports_with_hypotheses(conn, niche_id):
    where_niche = "AND niche_id = %s" if niche_id else ""
    params = (niche_id,) if niche_id else ()
    return conn.execute(
        f"""
        SELECT id::text AS id,
               niche_id,
               causal_hypotheses
        FROM strategist_reports
        WHERE causal_hypotheses IS NOT NULL
          AND jsonb_array_length(causal_hypotheses) > 0
          {where_niche}
        ORDER BY week_of DESC
        """,
        params,
    ).fetchall()


def _fetch_existing_arm_ids(conn, niche_id):
    rows = conn.execute(
        "SELECT arm_id FROM bandit_arms WHERE niche_id = %s LIMIT 200",
        (niche_id,),
    ).fetchall()
    return [str(r[0]) for r in rows]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="Actually queue experiments (default: dry-run)")
    ap.add_argument("--niche", default=None)
    ap.add_argument("--limit", type=int, default=100, help="Max hypotheses to process")
    ap.add_argument("--env-file", default="/opt/genlab/.env")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    _load_env(args.env_file)

    from genlab_core.scheduling.auto_experiment import (
        is_enabled,
        queue_pending_experiment,
    )
    from genlab_core.scheduling.auto_experiment_parser import (
        is_confidence_acceptable,
        parse_testable_prediction,
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
        reports = _fetch_reports_with_hypotheses(conn, args.niche)
        if not reports:
            logger.info("no strategist reports with hypotheses — exiting cleanly")
            return 0

        # Cache existing arms per niche to avoid re-querying.
        arms_by_niche: dict[str, list[str]] = {}

        total_seen = 0
        processed = 0
        queued = 0
        skipped_confidence = 0
        skipped_no_prediction = 0
        parse_errors = 0
        fatal_abort = False

        for report in reports:
            if fatal_abort:
                break
            hypotheses = report["causal_hypotheses"] or []
            if isinstance(hypotheses, str):
                try:
                    hypotheses = json.loads(hypotheses)
                except Exception:
                    hypotheses = []

            for idx, hyp in enumerate(hypotheses):
                if total_seen >= args.limit:
                    break
                total_seen += 1
                if not isinstance(hyp, dict):
                    continue

                confidence = str(hyp.get("confidence", "")).strip().lower()
                if not is_confidence_acceptable(confidence):
                    skipped_confidence += 1
                    continue

                prediction = str(hyp.get("testable_prediction", "")).strip()
                if not prediction:
                    skipped_no_prediction += 1
                    continue

                niche_id = report["niche_id"] or ""
                if niche_id not in arms_by_niche:
                    arms_by_niche[niche_id] = _fetch_existing_arm_ids(conn, niche_id)

                processed += 1
                spec, error_reason = parse_testable_prediction(
                    prediction,
                    niche_id,
                    arms_by_niche[niche_id],
                    hypothesis=str(hyp.get("hypothesis", "")),
                )
                if spec is None:
                    parse_errors += 1
                    logger.info(
                        "[parse] report=%s hyp=%d could not parse: %s",
                        report["id"][:8],
                        idx,
                        error_reason,
                    )
                    if error_reason in _FATAL_LLM_ERRORS:
                        logger.warning(
                            "fatal LLM error (%s) — aborting batch",
                            error_reason,
                        )
                        fatal_abort = True
                        break
                    continue

                if not args.apply:
                    print(
                        f"  [{niche_id}] hyp={idx} arms={spec.arms} "
                        f"shift={spec.expected_metric_shift:.3f} days={spec.duration_days}"
                    )
                    continue

                exp_id = queue_pending_experiment(
                    conn,
                    source_report_id=report["id"],
                    hypothesis_index=idx,
                    niche_id=niche_id,
                    spec=spec,
                    notes=str(hyp.get("hypothesis", ""))[:400],
                )
                if exp_id:
                    queued += 1
                    logger.info(
                        "[parse] queued exp=%s report=%s hyp=%d",
                        exp_id[:8],
                        report["id"][:8],
                        idx,
                    )

        if args.apply:
            conn.commit()

        logger.info(
            "DONE processed=%d queued=%d parse_errors=%d "
            "skipped_confidence=%d skipped_no_prediction=%d",
            processed,
            queued,
            parse_errors,
            skipped_confidence,
            skipped_no_prediction,
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

            write_durable_error("parse_testable_predictions", exc)
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
