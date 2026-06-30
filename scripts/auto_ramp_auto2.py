#!/usr/bin/env python3
"""AUTO #2 auto-ramp — weekly +10% rollout_pct per qualifying niche.

Operator decision #3 (2026-06-30): instead of cutting one PR per
niche per week to bump ``rollout_pct``, run a weekly cron that
auto-ramps any niche whose validation + calibration data say the
bandit is genuinely useful AND the gate's verdicts agree with the
operator.

Qualifying conditions (ALL must hold)
-------------------------------------
1. ``auto_approval_calibration.stats(niche_id, window_days=7)``
   returns ``agreement_rate > 0.90``.
2. The latest ``bandit_validation`` row for the niche has
   ``interpretation == 'bandit useful'`` (Spearman ≥ 0.3).
3. The validation row is fresh — within the last 48h.
4. Current ``rollout_pct`` for the niche is < ``--max-rollout-pct``
   (default 0.50, the operator's per-script cap).

When all hold, the script bumps ``rollout_pct`` by +0.10 (capped
at ``--max-rollout-pct``). Otherwise it logs the reason and skips.

Per-run cautious caps
---------------------
* Maximum 2 niches ramped per run. If 3+ niches qualify, the script
  ramps the 2 with the highest sample_count and logs the deferred
  ones. This bounds blast radius — if the calibration data turns
  out to be stale or wrong, only 2 niches at most have moved.
* Each ramp writes an audit row to a JSONL log
  (``.tmp/auto2_ramp_log.jsonl``) recording before/after rollout_pct,
  agreement_rate, interpretation, and the timestamp. Operator can
  read this to reconstruct any ramp history.
* ``--dry-run`` (default): no YAML writes, no audit log writes.
* ``--apply``: actually writes the YAML + audit log.

YAML preservation
-----------------
Uses ruamel.yaml in roundtrip mode (same loader as
``learning/config_updater.py``) so comments + blank lines + key
order in ``publishing.yaml`` are preserved across the read-mutate-
write cycle. The publishing.yaml files are heavily commented with
the ramp-ladder + kill-switch operator runbook; losing those
comments would silently delete production-critical guidance.

Schedule
--------
Designed to fire weekly via systemd timer (Mondays 09:30 UTC =
15:00 IST) — see ``deploy/systemd-phase2/genlab-auto2-ramp.*``.
The 1-week cadence matches each ramp-ladder step in the
publishing.yaml runbook so a niche that qualifies for 4 consecutive
weeks lands at the operator-cap (0.50) and then waits for manual
intervention.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("auto2_ramp")

# Canonical 5 niches. Mirrors ``genlab_core.pipeline.cli.NICHE_DIR_NAMES``
# but reproduced inline so the script imports cleanly even when the
# genlab_core package isn't installed (e.g. a thin venv).
NICHE_DIR_NAMES: dict[str, str] = {
    "ai_creators": "BlackboxBrief",
    "gaming": "CriticalRush",
    "sports": "ClutchWire",
    "movies": "SpliceReel",
    "anime": "FrameDrift",
}

# Hard-coded gate thresholds — mirror the rules the operator chose.
MIN_AGREEMENT_RATE = 0.90
USEFUL_INTERPRETATION = "bandit useful"
RAMP_INCREMENT = 0.10
DEFAULT_MAX_ROLLOUT_PCT = 0.50
VALIDATION_FRESHNESS_HOURS = 48
MAX_RAMPS_PER_RUN = 2


@dataclass
class RampDecision:
    """Outcome of a single niche evaluation."""

    niche_id: str
    publishing_yaml: Path
    current_rollout_pct: float
    proposed_rollout_pct: float
    agreement_rate: float
    sample_count: int
    interpretation: str
    interpretation_age_hours: float | None
    will_ramp: bool
    reason: str


def _genlab_root() -> Path:
    """Resolve GenLab workspace root — env var or walk up from this file."""
    env = os.environ.get("GENLAB_ROOT")
    if env:
        p = Path(env).resolve()
        if p.is_dir():
            return p
    # scripts/auto_ramp_auto2.py → scripts/ → GenLab/
    return Path(__file__).resolve().parent.parent


def _rt_yaml():
    """Build a roundtrip-preserving YAML handler. Lazy import so the
    script's --help / arg-parsing path doesn't require ruamel."""
    from ruamel.yaml import YAML

    yaml_rt = YAML(typ="rt")
    yaml_rt.preserve_quotes = True
    yaml_rt.default_flow_style = False
    yaml_rt.allow_unicode = True
    yaml_rt.width = 4096
    return yaml_rt


def read_rollout_pct(publishing_yaml: Path) -> tuple[float, Any | None]:
    """Return ``(rollout_pct, parsed_doc)`` from a publishing.yaml file.

    Returns ``(0.0, None)`` when the file is missing OR the
    ``auto_publish`` block doesn't carry ``rollout_pct``. The parsed
    doc is returned so the caller can mutate-and-write without a
    second load (preserves comments via the same handler instance).
    """
    if not publishing_yaml.exists():
        return 0.0, None
    yaml_rt = _rt_yaml()
    with open(publishing_yaml) as fh:
        doc = yaml_rt.load(fh)
    if doc is None:
        return 0.0, None
    auto_publish = (doc.get("auto_publish") or {}) if hasattr(doc, "get") else {}
    raw = auto_publish.get("rollout_pct", 0.0)
    try:
        return float(raw), doc
    except (TypeError, ValueError):
        return 0.0, doc


def write_rollout_pct(publishing_yaml: Path, doc: Any, new_rollout_pct: float) -> None:
    """Mutate ``doc.auto_publish.rollout_pct`` and atomic-write to disk.

    Requires the doc loaded via ``_rt_yaml()`` so comments + blanks
    survive. Uses a sibling ``.tmp`` then ``replace()`` for atomicity.
    """
    yaml_rt = _rt_yaml()
    auto_publish = doc["auto_publish"]
    auto_publish["rollout_pct"] = new_rollout_pct
    tmp_path = publishing_yaml.with_suffix(publishing_yaml.suffix + ".tmp")
    with open(tmp_path, "w") as fh:
        yaml_rt.dump(doc, fh)
    tmp_path.replace(publishing_yaml)


def _fetch_calibration_stats(niche_id: str) -> tuple[float, int]:
    """Return ``(agreement_rate, sample_count)`` for the niche.

    Returns ``(0.0, 0)`` when DB is unreachable / table empty / DSN
    missing — the same shape ``calibration_logger.stats`` returns on
    cold start. The gate downstream treats either as 'not qualified'
    so the ramp is naturally fail-closed.
    """
    try:
        from genlab_core.scheduling.calibration_logger import stats

        result = stats(niche_id=niche_id, window_days=7)
    except Exception as exc:  # noqa: BLE001 — fail-closed
        logger.warning("[auto2_ramp] calibration stats failed for %s: %s", niche_id, exc)
        return 0.0, 0
    return float(result.agreement_rate), int(result.sample_count)


def _fetch_latest_validation(niche_id: str) -> tuple[str, datetime | None]:
    """Return ``(interpretation, computed_at)`` for the latest
    ``bandit_validation`` row for *niche_id*.

    Returns ``("low signal", None)`` on any failure (missing DSN,
    table absent, no rows) — downstream gate then refuses to ramp.
    """
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return "low signal", None
    try:
        from genlab_core.storage.tenant_context import pg_connect
        from psycopg.rows import dict_row
    except ImportError:
        return "low signal", None
    try:
        with pg_connect(dsn, row_factory=dict_row, niche_id=niche_id, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT interpretation, computed_at
                    FROM bandit_validation
                    WHERE niche_id = %s
                    ORDER BY computed_at DESC
                    LIMIT 1
                    """,
                    (niche_id,),
                )
                row = cur.fetchone()
        if not row:
            return "low signal", None
        return str(row["interpretation"]), row["computed_at"]
    except Exception as exc:  # noqa: BLE001 — fail-closed
        logger.warning("[auto2_ramp] validation fetch failed for %s: %s", niche_id, exc)
        return "low signal", None


def evaluate_niche(
    niche_id: str,
    publishing_yaml: Path,
    *,
    max_rollout_pct: float,
    now: datetime | None = None,
    calibration_fn=_fetch_calibration_stats,
    validation_fn=_fetch_latest_validation,
) -> RampDecision:
    """Run all checks for *niche_id* and return a RampDecision.

    ``calibration_fn`` and ``validation_fn`` are injected for tests
    (so unit tests can stub DB calls without monkeypatching the
    psycopg connect path). Default values hit the live DB.

    Decision rules (any failure → ``will_ramp=False`` + a reason):
        1. Validation interpretation must be ``"bandit useful"``.
        2. Validation row must be < ``VALIDATION_FRESHNESS_HOURS`` old.
        3. Calibration ``agreement_rate`` must be > ``MIN_AGREEMENT_RATE``.
        4. Current ``rollout_pct`` must be < ``max_rollout_pct``.

    The proposed rollout_pct is always min(current + RAMP_INCREMENT,
    max_rollout_pct) so we never overshoot the cap even when the
    increment would push us past.
    """
    now = now or datetime.now(UTC)
    current_pct, _doc = read_rollout_pct(publishing_yaml)
    interpretation, computed_at = validation_fn(niche_id)
    agreement_rate, sample_count = calibration_fn(niche_id)

    age_hours: float | None = None
    if computed_at is not None:
        # Tolerate naive timestamps by assuming UTC (defensive — the
        # bandit_validation column is TIMESTAMPTZ but a missing tz on
        # a fake/test row shouldn't crash the evaluator).
        ts = computed_at if computed_at.tzinfo else computed_at.replace(tzinfo=UTC)
        age_hours = (now - ts).total_seconds() / 3600.0

    proposed_pct = min(current_pct + RAMP_INCREMENT, max_rollout_pct)

    # Gate cascade — first-failing reason wins so the operator log
    # always names exactly one cause per skipped niche.
    if interpretation != USEFUL_INTERPRETATION:
        reason = f"interpretation={interpretation!r} (need 'bandit useful')"
        will_ramp = False
    elif age_hours is None:
        reason = "no validation row found"
        will_ramp = False
    elif age_hours > VALIDATION_FRESHNESS_HOURS:
        reason = f"validation stale ({age_hours:.1f}h > {VALIDATION_FRESHNESS_HOURS}h)"
        will_ramp = False
    elif agreement_rate <= MIN_AGREEMENT_RATE:
        reason = (
            f"agreement_rate={agreement_rate:.3f} (need > {MIN_AGREEMENT_RATE}, n={sample_count})"
        )
        will_ramp = False
    elif current_pct >= max_rollout_pct:
        reason = f"rollout_pct={current_pct:.2f} already at/above cap {max_rollout_pct:.2f}"
        will_ramp = False
    elif proposed_pct <= current_pct:
        # Increment is zero or negative — defensive guard.
        reason = f"proposed_pct={proposed_pct:.2f} <= current_pct={current_pct:.2f}"
        will_ramp = False
    else:
        reason = (
            f"qualified: agreement={agreement_rate:.3f}, "
            f"n={sample_count}, age={age_hours:.1f}h, "
            f"{current_pct:.2f} → {proposed_pct:.2f}"
        )
        will_ramp = True

    return RampDecision(
        niche_id=niche_id,
        publishing_yaml=publishing_yaml,
        current_rollout_pct=current_pct,
        proposed_rollout_pct=proposed_pct,
        agreement_rate=agreement_rate,
        sample_count=sample_count,
        interpretation=interpretation,
        interpretation_age_hours=age_hours,
        will_ramp=will_ramp,
        reason=reason,
    )


def _publishing_yaml_for(genlab_root: Path, niche_id: str) -> Path:
    """Resolve the publishing.yaml path for *niche_id*."""
    dir_name = NICHE_DIR_NAMES.get(niche_id)
    if not dir_name:
        raise ValueError(f"Unknown niche_id {niche_id!r}")
    return genlab_root / dir_name / "config" / "publishing.yaml"


def _audit_log_path(genlab_root: Path) -> Path:
    """JSONL audit log path. Created on first --apply write."""
    return genlab_root / ".tmp" / "auto2_ramp_log.jsonl"


def _append_audit_row(audit_path: Path, row: dict[str, Any]) -> None:
    """Append a single JSONL row. Best-effort — never raises."""
    try:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with open(audit_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
    except OSError as exc:
        logger.warning("[auto2_ramp] audit log write failed: %s", exc)


def _pick_ramps(decisions: list[RampDecision]) -> tuple[list[RampDecision], list[RampDecision]]:
    """Apply the per-run cap. Returns ``(to_ramp, deferred)``.

    When more than ``MAX_RAMPS_PER_RUN`` niches qualify, the ones
    with the HIGHEST sample_count are picked (more data == more
    confidence in the signal). Ties broken by alphabetical niche_id
    for determinism.
    """
    eligible = [d for d in decisions if d.will_ramp]
    if len(eligible) <= MAX_RAMPS_PER_RUN:
        return eligible, []
    ranked = sorted(eligible, key=lambda d: (-d.sample_count, d.niche_id))
    return ranked[:MAX_RAMPS_PER_RUN], ranked[MAX_RAMPS_PER_RUN:]


def run(
    *,
    niche_filter: list[str] | None = None,
    max_rollout_pct: float = DEFAULT_MAX_ROLLOUT_PCT,
    apply: bool = False,
    genlab_root: Path | None = None,
    now: datetime | None = None,
    calibration_fn=_fetch_calibration_stats,
    validation_fn=_fetch_latest_validation,
) -> dict[str, Any]:
    """Run one auto-ramp pass.

    Returns a summary dict:
        {
            "decisions": [RampDecision-as-dict, ...],  # all niches evaluated
            "ramped":   [niche_id, ...],               # actually bumped
            "deferred": [niche_id, ...],               # qualified but capped
            "skipped":  [niche_id, ...],               # not qualified
            "applied":  bool,                          # mirrors --apply
            "max_rollout_pct": float,
        }

    When ``apply=False`` (default), no YAML writes and no audit
    rows are written — the summary still records what WOULD ramp.
    """
    genlab_root = genlab_root or _genlab_root()
    now = now or datetime.now(UTC)
    niches = niche_filter or list(NICHE_DIR_NAMES.keys())

    decisions: list[RampDecision] = []
    for niche_id in niches:
        publishing_yaml = _publishing_yaml_for(genlab_root, niche_id)
        decisions.append(
            evaluate_niche(
                niche_id,
                publishing_yaml,
                max_rollout_pct=max_rollout_pct,
                now=now,
                calibration_fn=calibration_fn,
                validation_fn=validation_fn,
            )
        )

    to_ramp, deferred = _pick_ramps(decisions)
    deferred_ids = [d.niche_id for d in deferred]
    skipped_ids = [d.niche_id for d in decisions if not d.will_ramp]

    ramped_ids: list[str] = []
    audit_path = _audit_log_path(genlab_root)
    for decision in to_ramp:
        logger.info(
            "[auto2_ramp] %s: %s",
            decision.niche_id,
            decision.reason,
        )
        if not apply:
            continue
        # Re-read with the roundtrip handler so we get a fresh doc to
        # mutate. evaluate_niche's read used a discarded doc.
        _current, doc = read_rollout_pct(decision.publishing_yaml)
        if doc is None:
            logger.warning(
                "[auto2_ramp] %s: publishing.yaml missing — cannot ramp",
                decision.niche_id,
            )
            continue
        write_rollout_pct(decision.publishing_yaml, doc, decision.proposed_rollout_pct)
        ramped_ids.append(decision.niche_id)
        _append_audit_row(
            audit_path,
            {
                "ts": now.isoformat(),
                "niche_id": decision.niche_id,
                "from_pct": decision.current_rollout_pct,
                "to_pct": decision.proposed_rollout_pct,
                "agreement_rate": decision.agreement_rate,
                "sample_count": decision.sample_count,
                "interpretation": decision.interpretation,
                "interpretation_age_hours": decision.interpretation_age_hours,
                "max_rollout_pct": max_rollout_pct,
            },
        )

    for decision in decisions:
        if decision.will_ramp:
            continue
        logger.info(
            "[auto2_ramp] %s: skip — %s",
            decision.niche_id,
            decision.reason,
        )

    for nid in deferred_ids:
        logger.info(
            "[auto2_ramp] %s: deferred — per-run cap of %d reached, will reconsider next week",
            nid,
            MAX_RAMPS_PER_RUN,
        )

    return {
        "decisions": [
            {
                "niche_id": d.niche_id,
                "current_pct": d.current_rollout_pct,
                "proposed_pct": d.proposed_rollout_pct,
                "agreement_rate": d.agreement_rate,
                "sample_count": d.sample_count,
                "interpretation": d.interpretation,
                "age_hours": d.interpretation_age_hours,
                "will_ramp": d.will_ramp,
                "reason": d.reason,
            }
            for d in decisions
        ],
        "ramped": ramped_ids,
        "deferred": deferred_ids,
        "skipped": skipped_ids,
        "applied": apply,
        "max_rollout_pct": max_rollout_pct,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="AUTO #2 weekly auto-ramp of publishing.yaml rollout_pct"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        dest="apply",
        action="store_false",
        help="Default. Print what WOULD ramp; do not write YAML or audit log.",
    )
    mode.add_argument(
        "--apply",
        dest="apply",
        action="store_true",
        help="Write YAML changes + audit log row for each ramped niche.",
    )
    parser.add_argument(
        "--niche-id",
        action="append",
        dest="niche_ids",
        default=None,
        help="Only consider this niche. Pass multiple times to filter to "
        "a subset. Default: all 5 niches.",
    )
    parser.add_argument(
        "--max-rollout-pct",
        type=float,
        default=DEFAULT_MAX_ROLLOUT_PCT,
        help=f"Per-script cap. Default {DEFAULT_MAX_ROLLOUT_PCT}.",
    )
    parser.set_defaults(apply=False)

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    summary = run(
        niche_filter=args.niche_ids,
        max_rollout_pct=args.max_rollout_pct,
        apply=args.apply,
    )

    mode_label = "APPLY" if summary["applied"] else "DRY-RUN"
    print(
        f"[{mode_label}] ramped={summary['ramped']} "
        f"deferred={summary['deferred']} skipped={summary['skipped']}"
    )
    # Exit 0 always — the script's job is observation+optional-action.
    # systemd surfaces "failed" only on unhandled exception (which we
    # log + swallow inside evaluate_niche).
    return 0


if __name__ == "__main__":
    sys.exit(main())
