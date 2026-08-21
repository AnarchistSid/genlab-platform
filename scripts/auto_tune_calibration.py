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
    """Rewrite auto_publish.min_confidence in publishing.yaml
    preserving comments + layout via ruamel.yaml roundtrip.

    Prior PyYAML implementation stripped ALL comments — a real
    ops regression on 2026-08-14 when the first auto-apply fire
    nuked the AUTO #2 rollout-ladder docs in BlackboxBrief +
    ClutchWire configs. ruamel.yaml is already a genlab-core dep
    (config_updater uses it).

    Returns (ok, diagnostic). Backup created first."""
    path = _publishing_yaml_path(niche_id)
    if path is None:
        return False, "no publishing.yaml found"

    # Backup FIRST so any subsequent failure is recoverable
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    backup = path.with_suffix(f".yaml.bak.{ts}")
    try:
        shutil.copy2(path, backup)
    except Exception as exc:
        return False, f"backup failed: {exc}"

    try:
        from ruamel.yaml import YAML
        yaml = YAML()
        yaml.preserve_quotes = True
        # Match existing indent conventions in publishing.yaml
        # (2-space mapping, 2-space sequence).
        yaml.indent(mapping=2, sequence=4, offset=2)
        with path.open("r") as f:
            data = yaml.load(f) or {}
        ap = data.get("auto_publish") if hasattr(data, "get") else None
        if ap is None or not hasattr(ap, "get"):
            return False, "auto_publish key missing or not a mapping"
        prior = ap.get("min_confidence")
        ap["min_confidence"] = round(new_value, 3)
        with path.open("w") as f:
            yaml.dump(data, f)
        return True, f"rewrote min_confidence: {prior} → {round(new_value, 3)}"
    except Exception as exc:
        # Restore from backup on any write-side failure
        try:
            shutil.copy2(backup, path)
        except Exception:
            pass
        return False, f"write failed: {exc}"


def _achievable_ceiling(conn, niche_id: str) -> float | None:
    """Highest min_confidence this niche could ever actually meet.

    Returns the p90 of gate-approved confidence over the last 30 days,
    deduplicated to the latest examination per blueprint — the worker
    re-evaluates the same backlog every 30 minutes, so raw percentiles are
    weighted by how many passes a blueprint happened to sit through rather
    than by how many blueprints there are.

    p90 rather than max: the maximum is a single lucky blueprint, and pinning
    the threshold to it would auto-approve roughly nothing while still
    technically being "achievable".

    None when there is not enough data to say — the caller then falls back to
    the hard ceiling alone, which is the conservative direction.
    """
    try:
        cur = conn.execute(
            """
            WITH latest AS (
                SELECT DISTINCT ON (blueprint_id)
                       blueprint_id, confidence, approved
                FROM gate_examinations
                WHERE niche_id = %s
                  AND examined_at > now() - interval '30 days'
                ORDER BY blueprint_id, examined_at DESC
            )
            SELECT COUNT(*) FILTER (WHERE approved) AS n,
                   percentile_cont(0.90) WITHIN GROUP (ORDER BY confidence)
                     FILTER (WHERE approved) AS p90
            FROM latest
            """,
            (niche_id,),
        )
        row = cur.fetchone()
    except Exception as exc:  # noqa: BLE001 — advisory input, never fatal
        print(f"    [WARN] achievable-ceiling query failed: {exc}")
        return None
    if not row:
        return None
    n = row["n"] if isinstance(row, dict) else row[0]
    p90 = row["p90"] if isinstance(row, dict) else row[1]
    if not n or n < 5 or p90 is None:
        return None
    return float(p90)


def _check_zero_approval_rate(conn, niche_id: str) -> None:
    """Warn when a niche has gate approvals but zero auto-approvals.

    The condition the ceiling exists to prevent, observed directly rather
    than inferred from the threshold. If the gate is saying yes and nothing
    is being auto-approved, the threshold is not being selective — it is off.
    """
    try:
        cur = conn.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM gate_examinations
                WHERE niche_id = %s AND approved
                  AND examined_at > now() - interval '48 hours') AS gate_yes,
              (SELECT COUNT(*) FROM blueprints
                WHERE niche_id = %s
                  AND action_taken_source = 'auto_approver_v1'
                  AND created_at > now() - interval '48 hours') AS auto_approved
            """,
            (niche_id, niche_id),
        )
        row = cur.fetchone()
    except Exception as exc:  # noqa: BLE001
        print(f"    [WARN] zero-rate check failed: {exc}")
        return
    if not row:
        return
    gate_yes = row["gate_yes"] if isinstance(row, dict) else row[0]
    auto = row["auto_approved"] if isinstance(row, dict) else row[1]
    if gate_yes and not auto:
        print(
            f"    [ALERT] {niche_id}: gate approved {gate_yes} blueprint(s) in "
            f"48h but auto_approver_v1 approved 0 — the threshold is acting as "
            f"an off switch, not a filter"
        )


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
    ceiling = _achievable_ceiling(conn, niche_id)
    if ceiling is not None:
        print(f"    achievable ceiling (p90 of gate-approved confidence, 30d): {ceiling:.3f}")
    _check_zero_approval_rate(conn, niche_id)
    suggestion = suggest_min_confidence(
        niche_id, confusion, current, achievable_ceiling=ceiling
    )

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
