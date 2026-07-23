#!/usr/bin/env python3
"""Auto-promote strategist causal_hypotheses to learning_findings.

Motivating incident (2026-07-23): 5 unreviewed strategist reports
from 2026-07-06 sit with 20+ hypotheses total, but learning_findings
has 0 rows — the writer prompt enrichment loop is dark because it
depends on operator review of each report.

Hypotheses are OBSERVATIONS (patterns + evidence + testable prediction),
not ACTIONS. They inform the writer prompt via
``intelligence.prompts.render_findings_block`` but never take side-
effect actions like creating new bandit arms. That means they're safe
to auto-promote *within a confidence filter* — medium/high only.

Contrast with ``apply_strategist_actions.py`` which materialises
proposals (arm_add, etc.) — those DO require operator accept because
they mutate the bandit space.

Confidence filter: only ``"medium"`` and ``"high"`` hypotheses promote.
``"low"`` stays behind for operator review — those are the ones with
weakest evidence and highest risk of being anti-signal.

Idempotency: each finding is keyed on ``(source_report_id, finding_text)``.
Running the script twice produces zero additional writes.

Usage:
    python scripts/auto_promote_hypotheses_to_findings.py           # dry-run
    python scripts/auto_promote_hypotheses_to_findings.py --apply   # write
    python scripts/auto_promote_hypotheses_to_findings.py --apply --niche gaming
    python scripts/auto_promote_hypotheses_to_findings.py --apply --include-low

Exit codes:
    0 — success (including nothing-to-do)
    3 — unhandled exception (durable-file traceback written)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "genlab-core" / "src"))

logger = logging.getLogger("auto_promote_hypotheses")


_ACCEPTED_CONFIDENCES: frozenset[str] = frozenset({"medium", "high"})


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


def _fetch_unpromoted_hypotheses(conn, niche_id, include_low):
    where_niche = "AND niche_id = %s" if niche_id else ""
    params = (niche_id,) if niche_id else ()

    sql = f"""
        SELECT id, niche_id, week_of, causal_hypotheses
        FROM strategist_reports
        WHERE causal_hypotheses IS NOT NULL
          AND jsonb_array_length(causal_hypotheses) > 0
          {where_niche}
        ORDER BY week_of DESC
    """
    rows = conn.execute(sql, params).fetchall()

    accepted = set(_ACCEPTED_CONFIDENCES)
    if include_low:
        accepted = accepted | {"low"}

    flattened = []
    for report in rows:
        report_id = report["id"]
        hypotheses = report["causal_hypotheses"] or []
        for hyp in hypotheses:
            if not isinstance(hyp, dict):
                continue
            confidence = str(hyp.get("confidence", "")).strip().lower()
            if confidence not in accepted:
                continue
            pattern = str(hyp.get("pattern", "")).strip()
            hypothesis = str(hyp.get("hypothesis", "")).strip()
            evidence = hyp.get("evidence") or []
            if not pattern and not hypothesis:
                continue
            if pattern and hypothesis:
                finding_text = f"{pattern} | {hypothesis}"
            else:
                finding_text = pattern or hypothesis
            flattened.append(
                {
                    "source_report_id": report_id,
                    "niche_id": report["niche_id"],
                    "week_of": report["week_of"],
                    "confidence": confidence,
                    "finding_text": finding_text,
                    "evidence_count": len(evidence)
                    if isinstance(evidence, list)
                    else 0,
                }
            )
    return flattened


def _find_existing(conn, source_report_id, finding_text):
    row = conn.execute(
        """
        SELECT 1 FROM learning_findings
        WHERE source_report_id = %s AND finding_text = %s
        LIMIT 1
        """,
        (source_report_id, finding_text),
    ).fetchone()
    return row is not None


def _insert_finding(conn, hyp):
    conn.execute(
        """
        INSERT INTO learning_findings
          (niche_id, finding_text, evidence_count, source, source_report_id, active)
        VALUES (%s, %s, %s, 'strategist', %s, TRUE)
        """,
        (
            hyp["niche_id"],
            hyp["finding_text"],
            hyp["evidence_count"],
            hyp["source_report_id"],
        ),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--niche", default=None)
    ap.add_argument("--include-low", action="store_true")
    ap.add_argument("--env-file", default="/opt/genlab/.env")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    _load_env(args.env_file)
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        logger.error("DATABASE_URL not set")
        return 2

    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        hypotheses = _fetch_unpromoted_hypotheses(
            conn, args.niche, args.include_low
        )
        if not hypotheses:
            logger.info("no eligible hypotheses to promote")
            return 0

        by_niche = {}
        for h in hypotheses:
            by_niche[h["niche_id"]] = by_niche.get(h["niche_id"], 0) + 1

        print(
            f"Eligible hypotheses: {len(hypotheses)} total across "
            f"{len(by_niche)} niches"
        )
        for nid, count in sorted(by_niche.items()):
            print(f"  {nid:12} {count} hypothesis(es)")

        if not args.apply:
            print("\nSample findings:")
            for h in hypotheses[:5]:
                print(f"  [{h['niche_id']}] {h['finding_text'][:100]}...")
            print("\nDRY RUN — run with --apply to write.")
            return 0

        promoted = 0
        skipped_existing = 0
        errors = 0
        for h in hypotheses:
            try:
                if _find_existing(conn, h["source_report_id"], h["finding_text"]):
                    skipped_existing += 1
                    continue
                _insert_finding(conn, h)
                promoted += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[promote] failed for niche=%s: %s",
                    h["niche_id"],
                    exc,
                )
                errors += 1
        conn.commit()

        logger.info(
            "DONE promoted=%d skipped_existing=%d errors=%d",
            promoted,
            skipped_existing,
            errors,
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

            write_durable_error("auto_promote_hypotheses", exc)
        except Exception as import_exc:  # noqa: BLE001
            print(f"(also failed to import durable_error: {import_exc})", file=sys.stderr)
            import traceback as _tb

            _tb.print_exc(file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(_main_with_durable_error())
