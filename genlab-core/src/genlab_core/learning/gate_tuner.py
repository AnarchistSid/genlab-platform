"""Auto-approval gate threshold tuner — closes the calibration feedback loop.

For background: ``calibration_logger`` writes (gate verdict, operator
action) pairs to ``auto_approval_calibration`` on every operator click.
``calibration_logger.stats(niche_id)`` aggregates them into a per-niche
confusion matrix (TP/TN/FP/FN). Until 2026-06-21 the stats were
WRITE-ONLY — no code path read them to update the gate's thresholds.
The loop was asymmetric: data captured, never consumed.

This module closes that loop. ``update_all_niches()`` runs nightly:
for each niche with ≥30 calibration samples, it computes the
false-positive and false-negative rates and shifts the per-niche
composite/virality thresholds by one conservative step
(``_THRESHOLD_STEP = 0.02``) in the direction that should reduce
disagreement. The override is persisted to a JSON file at
``GENLAB_ROOT/.tmp/gate_overrides.json``.

``auto_approval_gate.evaluate()`` reads the override via
``get_overrides_for_niche(niche_id)`` (extracted from the blueprint
dict) and uses it transparently — no caller needs to change.

Design choices:
- **Conservative step size** (0.02): one tick per nightly run. The
  tuner converges gradually rather than over-correcting, matching the
  spirit of the gate's "conservative by default" docstring.
- **Bounded thresholds**: composite ∈ [0.10, 0.50], virality ∈
  [0.005, 0.10]. Tuner can never push outside these bounds, so a
  pathological calibration data shift can't disable the gate entirely.
- **Cold-start floor**: identical to ``ready_for_enforcement`` (30
  samples). Below that, no adjustment — defaults stand.
- **Sensitivity band**: only adjust when FP/FN rate is >5 pp off the
  target (10%). Inside the band, no change — avoids thrashing on
  small-sample noise.
- **Per-niche storage**: each niche gets its own override entry. A
  gate that's too permissive for sports can tighten without affecting
  ai_creators.

Run via:
    python -m genlab_core.learning.gate_tuner
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

# Per-niche threshold bounds — the tuner can never push outside these.
# Defends against pathological calibration data (e.g. an operator who
# rejects everything for a day) from disabling the gate entirely.
_COMPOSITE_BOUNDS: Final[tuple[float, float]] = (0.10, 0.50)
_VIRALITY_BOUNDS: Final[tuple[float, float]] = (0.005, 0.10)

# Per-run adjustment magnitude. Conservative — let the tuner converge
# gradually across multiple nightly runs rather than over-correcting
# from a single noisy day.
_THRESHOLD_STEP: Final[float] = 0.02

# Target false-positive and false-negative rates. Calibrated to the
# `ready_for_enforcement` 90% agreement threshold — if we keep FP + FN
# rates at ~10%, agreement converges to 90% naturally.
_FP_RATE_TARGET: Final[float] = 0.10
_FN_RATE_TARGET: Final[float] = 0.10

# Sensitivity band: only adjust when FP/FN rate is more than 5 pp off
# the target. Inside the band, no change — prevents thrashing on
# small-sample noise.
_SENSITIVITY: Final[float] = 0.05

# Cold-start floor: match `ready_for_enforcement` so we never tune
# below the operator-data threshold that AUTO #2 also requires.
_MIN_SAMPLES_FOR_TUNING: Final[int] = 30


@dataclass(frozen=True)
class GateOverride:
    """Per-niche gate threshold override produced by the tuner.

    Captured at the moment the tuner derived the new values — the
    ``derived_from_samples`` + ``fp_rate`` + ``fn_rate`` fields let
    operators audit WHY a threshold moved, not just THAT it moved.
    """

    niche_id: str
    min_composite_score: float
    min_virality_score: float
    derived_from_samples: int
    fp_rate: float
    fn_rate: float
    computed_at: str


def _overrides_path() -> Path:
    """Resolve the runtime overrides JSON path.

    Defaults to ``<GenLab-root>/.tmp/gate_overrides.json``. Override
    via ``GATE_OVERRIDES_PATH`` for tests or alternate deployments —
    the test fixture uses tmp_path so the production file is never
    touched.
    """
    custom = os.getenv("GATE_OVERRIDES_PATH")
    if custom:
        return Path(custom)
    # Walk: src/genlab_core/learning/gate_tuner.py → ... → GenLab root
    here = Path(__file__).resolve()
    genlab_root = here.parents[4]
    return genlab_root / ".tmp" / "gate_overrides.json"


def compute_override(
    stats,  # CalibrationStats — annotated below to avoid import cycle at module-load
    current_composite: float,
    current_virality: float,
) -> GateOverride | None:
    """Compute the new per-niche threshold override from observed stats.

    Returns ``None`` when no adjustment is needed (within sensitivity
    band OR insufficient samples). The caller persists only the
    ``GateOverride`` instances; a ``None`` means "leave the existing
    override (or default) alone for this niche this run".

    Logic:
    - High FP rate (gate said yes, operator said no) → gate is too
      permissive → raise composite threshold by ``_THRESHOLD_STEP``.
    - High FN rate (gate said no, operator said yes) → gate is too
      restrictive → lower composite threshold by ``_THRESHOLD_STEP``.
    - Composite is the dominant signal (it's a richer score than the
      noise-floor virality_score). Virality threshold is held steady
      in this first pass — a future iteration could tune it separately
      once per-reason calibration data (Lever B) is available.

    The "FP wins over FN" tie-break in the branch order is intentional:
    if both rates are out-of-band on the same run (rare), prefer
    tightening the gate (false approvals are worse than false rejects
    for AUTO #2 trust).
    """
    if stats.sample_count < _MIN_SAMPLES_FOR_TUNING:
        return None  # Cold start — defaults stand.

    # FP rate: of the times the gate said "approve", how often did the
    # operator disagree (reject/revise/skip)? Bounded by the actual
    # gate-yes count to avoid divide-by-zero on a one-sided sample.
    gate_yes = stats.true_positives + stats.false_positives
    fp_rate = (stats.false_positives / gate_yes) if gate_yes > 0 else 0.0

    # FN rate: of the times the gate said "reject", how often did the
    # operator approve anyway?
    gate_no = stats.true_negatives + stats.false_negatives
    fn_rate = (stats.false_negatives / gate_no) if gate_no > 0 else 0.0

    new_composite = current_composite
    if fp_rate > _FP_RATE_TARGET + _SENSITIVITY:
        # Too permissive: tighten by one step.
        new_composite = min(_COMPOSITE_BOUNDS[1], current_composite + _THRESHOLD_STEP)
    elif fn_rate > _FN_RATE_TARGET + _SENSITIVITY:
        # Too restrictive: relax by one step.
        new_composite = max(_COMPOSITE_BOUNDS[0], current_composite - _THRESHOLD_STEP)

    # Hold virality steady in this first iteration — see docstring.
    new_virality = current_virality

    # If nothing moved, signal no-change via None so the caller can
    # decide whether to log/skip vs. persist.
    if (
        abs(new_composite - current_composite) < 1e-9
        and abs(new_virality - current_virality) < 1e-9
    ):
        return None

    return GateOverride(
        niche_id=stats.niche_id,
        min_composite_score=round(new_composite, 4),
        min_virality_score=round(new_virality, 4),
        derived_from_samples=stats.sample_count,
        fp_rate=round(fp_rate, 4),
        fn_rate=round(fn_rate, 4),
        computed_at=datetime.now(UTC).isoformat(),
    )


def load_overrides() -> dict[str, dict]:
    """Load current per-niche overrides from the runtime JSON file.

    Returns ``{}`` on missing file, malformed JSON, or any I/O failure.
    Best-effort by design — the gate falls back to its defaults when
    no override is available, matching pre-2026-06-21 behavior.
    """
    path = _overrides_path()
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("[gate_tuner] failed to load overrides from %s: %s", path, exc)
        return {}


def get_overrides_for_niche(niche_id: str) -> tuple[float | None, float | None]:
    """Return ``(min_composite_score, min_virality_score)`` overrides for a niche.

    Returns ``(None, None)`` when no override exists for this niche —
    caller uses gate defaults. The auto-approval gate calls this once
    per ``evaluate()`` invocation.

    Performance note: this re-reads the JSON file on every call. The
    file is small (<5KB) and the gate is not on the per-publish hot
    path. If this ever becomes hot, wrap with the mtime cache helper
    from ``server.core.yaml_cache``.
    """
    if not niche_id:
        return (None, None)
    overrides = load_overrides()
    entry = overrides.get(niche_id)
    if not entry:
        return (None, None)
    return (
        entry.get("min_composite_score"),
        entry.get("min_virality_score"),
    )


def _write_overrides_atomically(overrides: dict[str, dict]) -> None:
    """Persist overrides via a tmp-file rename — partial writes won't
    corrupt the runtime file if the process is killed mid-write."""
    path = _overrides_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    with open(tmp_path, "w") as f:
        json.dump(overrides, f, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def update_all_niches(*, window_days: int = 30) -> dict[str, GateOverride | None]:
    """For each niche with sufficient calibration data, compute + persist
    threshold overrides. Designed for nightly cron use.

    Returns a ``{niche_id: GateOverride | None}`` map. A ``None`` value
    means no change was needed for that niche this run. Operators can
    audit the return value to understand what the tuner did (and didn't)
    on a given run.
    """
    # Lazy imports to avoid circular dependencies + keep import-time
    # cost low for callers that only use load_overrides /
    # get_overrides_for_niche.
    from genlab_core.scheduling.auto_approval_gate import (
        _DEFAULT_MIN_COMPOSITE_SCORE,
        _DEFAULT_MIN_VIRALITY_SCORE,
    )
    from genlab_core.scheduling.calibration_logger import stats_all_niches

    overrides_now = load_overrides()
    results: dict[str, GateOverride | None] = {}

    all_stats = stats_all_niches(window_days=window_days)
    for niche_id, stats in all_stats.items():
        current_composite = overrides_now.get(niche_id, {}).get(
            "min_composite_score", _DEFAULT_MIN_COMPOSITE_SCORE
        )
        current_virality = overrides_now.get(niche_id, {}).get(
            "min_virality_score", _DEFAULT_MIN_VIRALITY_SCORE
        )
        override = compute_override(stats, current_composite, current_virality)
        results[niche_id] = override
        if override is not None:
            overrides_now[niche_id] = {
                "min_composite_score": override.min_composite_score,
                "min_virality_score": override.min_virality_score,
                "derived_from_samples": override.derived_from_samples,
                "fp_rate": override.fp_rate,
                "fn_rate": override.fn_rate,
                "computed_at": override.computed_at,
            }

    # Only write when something actually changed — avoids touching the
    # file's mtime on no-op runs (matters if anyone watches the file).
    if any(o is not None for o in results.values()):
        _write_overrides_atomically(overrides_now)
        logger.info(
            "[gate_tuner] updated %d/%d niches: %s",
            sum(1 for o in results.values() if o is not None),
            len(results),
            ", ".join(n for n, o in results.items() if o is not None),
        )
    else:
        logger.info("[gate_tuner] no adjustments needed across %d niches", len(results))

    return results


if __name__ == "__main__":
    # CLI entry: ``python -m genlab_core.learning.gate_tuner``
    # Wire into the nightly cron timer separately.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    results = update_all_niches()
    for niche, override in results.items():
        if override is None:
            print(f"  {niche}: no change")
            continue
        print(
            f"  {niche}: composite={override.min_composite_score} "
            f"virality={override.min_virality_score} "
            f"(fp_rate={override.fp_rate:.2%}, "
            f"fn_rate={override.fn_rate:.2%}, "
            f"n={override.derived_from_samples})"
        )
