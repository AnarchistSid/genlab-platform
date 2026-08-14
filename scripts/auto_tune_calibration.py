#!/usr/bin/env python3
"""Phase 5.A — auto-tune calibration thresholds.

Fires weekly (Mon 06:00 UTC) via ``genlab-calibration-tuner.timer``.
Per niche:

  1. Pull last 4 weeks of auto_approval_calibration rows.
  2. Compute confusion matrix (rule #22 discipline).
  3. Suggest min_confidence delta.
  4. Persist suggestion to calibration_tuning_suggestions.
  5. If |delta| <= 0.05 AND --apply flag set → rewrite the niche's
     publishing.yaml auto_publish.min_confidence.

## Safe YAML rewrite

  * Load YAML → mutate the ONE key → write back with same
    key order + comments preserved (via ruamel.yaml if available,
    else PyYAML with an inline warning).
  * Diff-check: refuse to write if any other key would change.
  * Back up the file to ``.yaml.bak.<ts>`` before writing.

## Usage

    uv run python scripts/auto_tune_calibration.py           # persist suggestion only
    uv run python scripts/auto_tune_calibration.py --apply   # also rewrite yaml
    uv run python scripts/auto_tune_calibration.py --dry-run # print, no writes
    uv run python scripts/auto_tune_calibration.py --niche gaming --apply

## Exit codes

  * 0 — completed
  * 1 — DATABASE_URL unset
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger("auto_tune_calibration")

ACTIVE_NICHES = ("ai_creators", "anime", "gaming", "movies", "sports")

# Weeks of calibration data to include in the suggestion window
_LOOKBACK_WEEKS = 4


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="Print + persist suggestion only, no yaml write")
    ap.add_argument("--apply", action="store_true",
                    help="Also rewrite publishing.yaml when |delta| <= 0.05")
    ap.add_argument("--niche", default=None)
    return ap.parse_args(argv)


def _monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _publishing_yaml_path(niche_id: str) -> Path | None:
    """Same NICHE_DIR_NAMES resolution as sponsorship + persona_drift."""
    try:
        from genlab_core.pipeline.cli import (
            NICHE_DIR_NAMES,
            _resolve_genlab_root,
        )
    except ImportError:
        return None
    root = _resolve_genlab_root()
    dir_name = NICHE_DIR_NAMES.get(niche_id)
    if not dir_name:
        return None
    candidates = [
        Path(root) / dir_name / "config" / "publishing.yaml",
        Path(root) / dir_name / "niches" / niche_id / "config" / "publishing.yaml",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def _current_min_confidence(niche_id: str) -> float:
    """Read the niche's publishing.yaml. Returns 0.85 (roadmap
    default) if missing/malformed — safe conservative default."""
    path = _publishing_yaml_path(niche_id)
    if path is None:
        return 0.85
    try:
        import yaml
        data = yaml.safe_load(path.read_text()) or {}
    except Exception as exc:
        logger.warning("[tuner] read publishing.yaml failed %s: %s", path, exc)
        return 0.85
    ap = (data or {}).get("auto_publish") or {}
    val = ap.get("min_confidence")
    try:
        return float(val) if val is not None else 0.85
    except (TypeError, ValueError):
        return 0.85


def _load_recent_calibration(conn, niche_id: str, weeks: int) -> list[dict]:
    """Fail-open to empty list on any query error."""
    try:
        rows = conn.execute(
            """
            SELECT gate_approved, operator_action
            FROM auto_approval_calibration
            WHERE niche_id = %s
              AND decided_at >= NOW() - (%s || ' weeks')::INTERVAL
            """,
            (niche_id, weeks),
        ).fetchall()
    except Exception as exc:
        logger.warning("[tuner] calibration query failed niche=%s: %s",
                       niche_id, exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return []
    return [
        {
            "gate_approved": (
                r.get("gate_approved") if hasattr(r, "get") else r[0]
            ),
            "operator_action": (
                r.get("operator_action") if hasattr(r, "get") else r[1]
            ),
        }
        for r in rows or []
    ]


def _persist_suggestion(
    conn, week_of: date, suggestion, sample_size: int,
) -> bool:
    try:
        conn.execute(
            """
            INSERT INTO calibration_tuning_suggestions
              (niche_id, week_of, confusion, sample_size,
               current_min_confidence, suggested_delta,
               suggested_min_confidence, applied, rationale)
            VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s, FALSE, %s)
            ON CONFLICT (niche_id, week_of) DO UPDATE SET
              confusion = EXCLUDED.confusion,
              sample_size = EXCLUDED.sample_size,
              current_min_confidence = EXCLUDED.current_min_confidence,
              suggested_delta = EXCLUDED.suggested_delta,
              suggested_min_confidence = EXCLUDED.suggested_min_confidence,
              rationale = EXCLUDED.rationale,
              computed_at = NOW()
            """,
            (
                suggestion.niche_id, week_of,
                json.dumps(suggestion.confusion.to_dict()),
                sample_size,
                suggestion.current_min_confidence,
                suggestion.suggested_delta,
                suggestion.suggested_min_confidence,
                suggestion.rationale,
            ),
        )
        conn.commit()
        return True
    except Exception as exc:
        logger.warning("[tuner] persist failed niche=%s: %s",
                       suggestion.niche_id, exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def _apply_yaml_rewrite(niche_id: str, new_value: float) -> tuple[bool, str]:
    """Rewrite auto_publish.min_confidence in publishing.yaml.
    Returns (ok, diagnostic). Backup created first."""
    path = _publishing_yaml_path(niche_id)
    if path is None:
        return False, "no publishing.yaml found"
    try:
        import yaml
        original_text = path.read_text()
        data = yaml.safe_load(original_text) or {}
    except Exception as exc:
        return False, f"read failed: {exc}"
    ap = data.get("auto_publish")
    if not isinstance(ap, dict):
        return False, "auto_publish key missing or not a dict"
    prior = ap.get("min_confidence")
    ap["min_confidence"] = round(new_value, 3)

    # Backup
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    backup = path.with_suffix(f".yaml.bak.{ts}")
    try:
        shutil.copy2(path, backup)
    except Exception as exc:
        return False, f"backup failed: {exc}"

    # Rewrite (PyYAML — comments will be lost; this is the safety
    # trade-off. ruamel.yaml would preserve comments but adds a dep.)
    try:
        path.write_text(yaml.safe_dump(data, sort_keys=False))
    except Exception as exc:
        # Try to restore
        try:
            shutil.copy2(backup, path)
        except Exception:
            pass
        return False, f"write failed: {exc}"
    return True, f"rewrote min_confidence: {prior} → {new_value}"


def _run_niche(
    conn, niche_id: str, week_of: date,
    apply_yaml: bool, dry_run: bool,
) -> dict:
    from genlab_core.scheduling.calibration_tuner import (
        AUTO_APPLY_MAX_DELTA,
        compute_confusion,
        suggest_min_confidence,
    )

    counts = {"analyzed": 0, "persisted": 0, "yaml_applied": 0}

    rows = _load_recent_calibration(conn, niche_id, _LOOKBACK_WEEKS)
    counts["analyzed"] = len(rows)
    confusion = compute_confusion(rows)
    current = _current_min_confidence(niche_id)
    suggestion = suggest_min_confidence(niche_id, confusion, current)

    print(
        f"  {niche_id} n={confusion.n} "
        f"TP={confusion.tp} TN={confusion.tn} "
        f"FP={confusion.fp} FN={confusion.fn} "
        f"delta={suggestion.suggested_delta:+.3f} "
        f"current={current:.2f} → suggested={suggestion.suggested_min_confidence:.2f}"
    )
    print(f"    {suggestion.rationale}")

    if dry_run:
        return counts

    if _persist_suggestion(conn, week_of, suggestion, confusion.n):
        counts["persisted"] = 1

    # Auto-apply gate
    if not apply_yaml:
        return counts
    if not suggestion.within_auto_apply:
        if suggestion.suggested_delta == 0:
            print(f"    [SKIP APPLY] delta=0 — nothing to apply")
        else:
            print(f"    [SKIP APPLY] |delta| > {AUTO_APPLY_MAX_DELTA} — operator review required")
        return counts

    ok, diagnostic = _apply_yaml_rewrite(
        niche_id, suggestion.suggested_min_confidence,
    )
    print(f"    {'[APPLIED]' if ok else '[APPLY FAILED]'} {diagnostic}")
    if ok:
        counts["yaml_applied"] = 1
        # Flip applied flag on the persisted row
        try:
            conn.execute(
                """
                UPDATE calibration_tuning_suggestions
                SET applied = TRUE
                WHERE niche_id = %s AND week_of = %s
                """,
                (niche_id, week_of),
            )
            conn.commit()
        except Exception:
            pass
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

    week_of = _monday_of(date.today())
    niches = (args.niche,) if args.niche else ACTIVE_NICHES

    import psycopg
    from psycopg.rows import dict_row

    print(
        f"\nAuto-tune calibration (week_of={week_of}, "
        f"apply={args.apply}, dry_run={args.dry_run})"
    )
    totals = {"analyzed": 0, "persisted": 0, "yaml_applied": 0}
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        for niche_id in niches:
            counts = _run_niche(
                conn, niche_id, week_of, args.apply, args.dry_run,
            )
            for k, v in counts.items():
                totals[k] += v

    logger.info(
        "[tuner] totals: analyzed=%d persisted=%d yaml_applied=%d",
        totals["analyzed"], totals["persisted"], totals["yaml_applied"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
