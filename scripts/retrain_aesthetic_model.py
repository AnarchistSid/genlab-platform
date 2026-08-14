#!/usr/bin/env python3
"""Phase 4.B session 2 — monthly aesthetic model retrainer.

Fires monthly (1st @ 05:00 UTC) via
``genlab-aesthetic-retrainer.timer``. Per niche:

  1. Load labeled examples from ``aesthetic_training_data``.
  2. Fit logistic regression via
     :func:`genlab_core.quality.aesthetic_trainer.train_model`.
  3. If AUC > ``AUC_PROMOTE_THRESHOLD`` (default 0.60), persist a
     new row in ``aesthetic_model_versions`` + flip is_active
     inside a transaction (previous is_active row → False).
  4. Below threshold: log + skip (don't demote the currently
     active model — better to keep an old-but-known-good model
     than lose the wire entirely).

## Idempotency

UNIQUE (niche_id, version). Version is computed as MAX(version) + 1
per niche. Re-running the same day silently produces version+1
even if the AUC is identical — that's fine because is_active is
the load-bearing pointer.

## Usage

    uv run python scripts/retrain_aesthetic_model.py
    uv run python scripts/retrain_aesthetic_model.py --dry-run
    uv run python scripts/retrain_aesthetic_model.py --niche gaming
    uv run python scripts/retrain_aesthetic_model.py --auc-threshold 0.65

## Exit codes

  * 0 — completed (any subset of niches trained)
  * 1 — DATABASE_URL unset
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

logger = logging.getLogger("retrain_aesthetic_model")

ACTIVE_NICHES = ("ai_creators", "anime", "gaming", "movies", "sports")

# Roadmap gate: only promote a new model if AUC > 0.60. Below
# that, the model is noise + risks degrading the score signal.
AUC_PROMOTE_THRESHOLD = 0.60


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--niche", default=None)
    ap.add_argument("--auc-threshold", type=float,
                    default=AUC_PROMOTE_THRESHOLD)
    return ap.parse_args(argv)


def _load_training_rows(conn, niche_id: str):
    """Fetch all labeled rows for a niche. Fail-open to []."""
    try:
        rows = conn.execute(
            """
            SELECT label, features
            FROM aesthetic_training_data
            WHERE niche_id = %s
            """,
            (niche_id,),
        ).fetchall()
    except Exception as exc:
        logger.warning(
            "[retrain] training-row query failed niche=%s: %s",
            niche_id, exc,
        )
        try:
            conn.rollback()
        except Exception:
            pass
        return []
    return [
        {
            "label": r.get("label") if hasattr(r, "get") else r[0],
            "features": r.get("features") if hasattr(r, "get") else r[1],
        }
        for r in rows or []
    ]


def _next_version(conn, niche_id: str) -> int:
    """MAX(version)+1 or 1 if no prior model. Fail-open to 1."""
    try:
        row = conn.execute(
            """
            SELECT COALESCE(MAX(version), 0)::int AS v
            FROM aesthetic_model_versions
            WHERE niche_id = %s
            """,
            (niche_id,),
        ).fetchone()
        v = row.get("v") if hasattr(row, "get") else row[0]
        return int(v) + 1
    except Exception as exc:
        logger.warning(
            "[retrain] version-lookup failed niche=%s: %s", niche_id, exc,
        )
        return 1


def _persist_and_promote(
    conn, niche_id: str, version: int, model,
) -> bool:
    """Insert new model row + flip is_active. Transactional so a
    mid-op crash doesn't leave two active rows for the niche."""
    try:
        # Demote all existing active rows for this niche
        conn.execute(
            """
            UPDATE aesthetic_model_versions
            SET is_active = FALSE
            WHERE niche_id = %s AND is_active = TRUE
            """,
            (niche_id,),
        )
        # Insert new row + mark active
        conn.execute(
            """
            INSERT INTO aesthetic_model_versions
              (niche_id, version, coefficients, intercept, auc,
               n_train, n_test, is_active)
            VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s, TRUE)
            """,
            (
                niche_id, version,
                json.dumps(model.coefficients),
                model.intercept, model.auc,
                model.n_train, model.n_test,
            ),
        )
        conn.commit()
        return True
    except Exception as exc:
        logger.warning(
            "[retrain] persist/promote failed niche=%s: %s",
            niche_id, exc,
        )
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def _run_niche(
    conn, niche_id: str, auc_threshold: float, dry_run: bool,
) -> dict:
    from genlab_core.quality.aesthetic_trainer import train_model

    counts = {"samples": 0, "trained": 0, "promoted": 0, "skipped_auc": 0}
    rows = _load_training_rows(conn, niche_id)
    counts["samples"] = len(rows)
    if not rows:
        return counts

    model = train_model(niche_id, rows)
    if model is None:
        return counts
    counts["trained"] = 1

    print(
        f"  {niche_id}: n_train={model.n_train} n_test={model.n_test} "
        f"AUC={model.auc:.3f}"
    )

    if model.auc <= auc_threshold:
        counts["skipped_auc"] = 1
        print(
            f"    skip promote — AUC {model.auc:.3f} <= threshold {auc_threshold}"
        )
        return counts

    if dry_run:
        print(f"    [DRY] would promote v? with AUC={model.auc:.3f}")
        counts["promoted"] = 1
        return counts

    version = _next_version(conn, niche_id)
    if _persist_and_promote(conn, niche_id, version, model):
        counts["promoted"] = 1
        print(f"    promoted v{version} (AUC={model.auc:.3f})")
    return counts


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

    niches = (args.niche,) if args.niche else ACTIVE_NICHES

    import psycopg
    from psycopg.rows import dict_row

    totals = {"samples": 0, "trained": 0, "promoted": 0, "skipped_auc": 0}
    print(f"\nRetraining aesthetic models (AUC threshold={args.auc_threshold})")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        for niche_id in niches:
            counts = _run_niche(
                conn, niche_id, args.auc_threshold, args.dry_run,
            )
            for k, v in counts.items():
                totals[k] += v

    logger.info(
        "[retrain] totals: samples=%d trained=%d promoted=%d skipped_auc=%d",
        totals["samples"], totals["trained"],
        totals["promoted"], totals["skipped_auc"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
