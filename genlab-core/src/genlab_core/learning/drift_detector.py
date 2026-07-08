"""Bandit arm drift detector — catches "we kept exploiting a dead arm".

The bandit_arms table stores lifetime (alpha, beta) posteriors. When
a hook style or content type that worked well 30 days ago stops
performing (algorithm change, content saturation, audience shift),
the bandit keeps exploiting it for weeks because the lifetime
posterior barely moves on a small daily delta.

Lever L (2026-06-21) closes this blind spot. Daily snapshots already
land at ``$BANDIT_SNAPSHOT_DIR/bandit_arms_YYYYMMDD.csv`` via
``scripts/bandit_arms_snapshot.sh``. This module computes:

1. **Recent window rate** — alpha/beta deltas over the last 7 days
2. **Baseline window rate** — alpha/beta deltas over the last 30 days
3. **z-score** of recent vs baseline using binomial variance approx

When ``|z| > 2.0``, emit a DriftSignal. Operators (or a future
automated reset job) can act on the alert.

Design choices:

- **Pure functions for the math** — testable without snapshot files,
  swappable for different drift formulas in the future.
- **Tolerant of missing snapshots** — gaps in the daily series
  fall through to the next-available snapshot rather than crashing.
- **CSV-only data source** — no new tables, no schema migration.
  Operators can ``ls /mnt/genlab-media/snapshots/`` to see what data
  is available; debugging is a ``head`` / ``diff`` away.
- **Read-only** — this PR ships detection + alerting only. Automated
  arm resets are a follow-up that depends on operator trust in the
  z-score thresholds (typically 2-4 weeks of shadow data).

Run via:
    python -m genlab_core.learning.drift_detector

Optional flags:
    --snapshot-dir /path/to/snapshots  # override default
    --recent-days 7                     # default 7
    --baseline-days 30                  # default 30
    --threshold-z 2.0                   # default 2.0
"""

from __future__ import annotations

import csv
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

# Defaults — tunable per-call. Conservative bounds match the
# "ready_for_enforcement" floor used by gate_tuner (Lever A) so we
# don't false-alert on cold-start arms.
_DEFAULT_RECENT_DAYS: Final[int] = 7
_DEFAULT_BASELINE_DAYS: Final[int] = 30
_DEFAULT_THRESHOLD_Z: Final[float] = 2.0

# Don't compute drift on arms with too few observations in either
# window — the binomial variance approximation breaks down at low
# counts. Matches the minimum n_plays gate_tuner expects.
_MIN_OBS_FOR_DRIFT: Final[int] = 10


@dataclass(frozen=True)
class DriftSignal:
    """Per-arm drift alert from comparing recent vs baseline rewards.

    Fields:
        arm_id: The arm's identifier (e.g. ``style:gaming:question``).
        niche_id: Which niche the arm belongs to.
        recent_rate: Win rate over the recent window
            (alpha_delta / (alpha_delta + beta_delta)).
        baseline_rate: Win rate over the baseline window.
        z_score: (recent_rate - baseline_rate) / baseline_stderr.
            Positive = arm performing BETTER than baseline (less concerning).
            Negative = arm performing WORSE (the concerning case —
            operators should consider re-exploration).
        recent_obs: Total observations in recent window (alpha_delta + beta_delta).
        baseline_obs: Total observations in baseline window.
        computed_at: ISO timestamp of when the signal was computed.
    """

    arm_id: str
    niche_id: str
    recent_rate: float
    baseline_rate: float
    z_score: float
    recent_obs: int
    baseline_obs: int
    computed_at: str

    @property
    def is_significant(self) -> bool:
        """Default-threshold significance check (|z| >= 2.0)."""
        return abs(self.z_score) >= _DEFAULT_THRESHOLD_Z

    @property
    def is_regression(self) -> bool:
        """True when recent < baseline (arm is degrading) — the case
        operators care about most. A positive z-score (arm improving)
        is informational; a negative z-score below threshold is a
        ``re-explore this arm`` signal."""
        return self.z_score < 0 and self.is_significant


def compute_arm_drift(
    arm_id: str,
    niche_id: str,
    snapshots: list[dict],
    *,
    recent_days: int = _DEFAULT_RECENT_DAYS,
    baseline_days: int = _DEFAULT_BASELINE_DAYS,
) -> DriftSignal | None:
    """Compute the drift signal for one arm from a chronological snapshot list.

    ``snapshots`` is a list of dicts with at least ``alpha``, ``beta``,
    and ``n_plays`` keys, ordered oldest-first. Returns ``None`` when:
    - Not enough snapshots to span both windows
    - Either window has fewer than ``_MIN_OBS_FOR_DRIFT`` observations
    - Baseline window has no usable stderr (rate too close to 0 or 1)

    The math:
        recent_rate = (α_today - α_recent_start) / total_delta
        baseline_rate = (α_today - α_baseline_start) / total_delta
        baseline_stderr ≈ sqrt(p*(1-p)/n)  (binomial approximation)
        z = (recent_rate - baseline_rate) / baseline_stderr

    A negative z below ``-2.0`` is the "arm regressed" alert.
    A positive z above ``+2.0`` is informational (arm improving).
    """
    if len(snapshots) < 2:
        return None

    today = snapshots[-1]
    today_alpha = float(today.get("alpha", 0) or 0)
    today_beta = float(today.get("beta", 0) or 0)

    # Find the snapshot at recent_days ago. If not enough history,
    # use the oldest available — this gives a partial-window signal
    # that's still useful as long as obs count is sufficient.
    recent_idx = max(0, len(snapshots) - recent_days - 1)
    recent_start = snapshots[recent_idx]
    recent_alpha_delta = today_alpha - float(recent_start.get("alpha", 0) or 0)
    recent_beta_delta = today_beta - float(recent_start.get("beta", 0) or 0)
    recent_total = recent_alpha_delta + recent_beta_delta

    baseline_idx = max(0, len(snapshots) - baseline_days - 1)
    baseline_start = snapshots[baseline_idx]
    baseline_alpha_delta = today_alpha - float(baseline_start.get("alpha", 0) or 0)
    baseline_beta_delta = today_beta - float(baseline_start.get("beta", 0) or 0)
    baseline_total = baseline_alpha_delta + baseline_beta_delta

    # Need enough observations in both windows for the variance
    # approximation to be meaningful.
    if recent_total < _MIN_OBS_FOR_DRIFT or baseline_total < _MIN_OBS_FOR_DRIFT:
        return None

    recent_rate = recent_alpha_delta / recent_total
    baseline_rate = baseline_alpha_delta / baseline_total

    # Binomial variance: var(p̂) = p(1-p)/n. Approximation OK when
    # n*p and n*(1-p) are both ≥ 5 (which our _MIN_OBS gate handles).
    baseline_var = baseline_rate * (1 - baseline_rate) / baseline_total
    baseline_stderr = baseline_var**0.5

    # Avoid divide-by-zero when the baseline rate is exactly 0 or 1
    # (e.g. an arm with 100% wins for 30 days — rare but possible).
    if baseline_stderr < 1e-6:
        return None

    z_score = (recent_rate - baseline_rate) / baseline_stderr

    return DriftSignal(
        arm_id=arm_id,
        niche_id=niche_id,
        recent_rate=round(recent_rate, 4),
        baseline_rate=round(baseline_rate, 4),
        z_score=round(z_score, 3),
        recent_obs=int(recent_total),
        baseline_obs=int(baseline_total),
        computed_at=datetime.now(UTC).isoformat(),
    )


def _snapshot_dir() -> Path:
    """Resolve the snapshot directory (BANDIT_SNAPSHOT_DIR override or
    the prod default)."""
    custom = os.getenv("BANDIT_SNAPSHOT_DIR")
    if custom:
        return Path(custom)
    return Path("/mnt/genlab-media/snapshots")


def _parse_snapshot_filename(name: str) -> datetime | None:
    """Extract the snapshot date from ``bandit_arms_YYYYMMDD.csv``."""
    if not name.startswith("bandit_arms_") or not name.endswith(".csv"):
        return None
    try:
        date_str = name[len("bandit_arms_") : -len(".csv")]
        return datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=UTC)
    except ValueError:
        return None


def load_snapshots(
    snapshot_dir: Path,
    *,
    lookback_days: int = _DEFAULT_BASELINE_DAYS,
) -> dict[tuple[str, str], list[dict]]:
    """Load CSV snapshots from ``snapshot_dir``, keyed by (niche_id, arm_id).

    Returns ``{(niche_id, arm_id): [day_0_row, day_1_row, ...]}`` with
    days ordered oldest-first. Snapshots are filtered to the last
    ``lookback_days`` days so we don't process ancient data.

    Returns ``{}`` on missing directory / no snapshots / read errors.
    Best-effort by design — missing data must not crash the detector.
    """
    if not snapshot_dir.exists() or not snapshot_dir.is_dir():
        logger.debug("[drift] snapshot dir not found: %s", snapshot_dir)
        return {}

    cutoff = datetime.now(UTC) - timedelta(days=lookback_days + 1)

    # Sort snapshots by date so chronological order is correct.
    files: list[tuple[datetime, Path]] = []
    for path in snapshot_dir.iterdir():
        if not path.is_file():
            continue
        date = _parse_snapshot_filename(path.name)
        if date is None or date < cutoff:
            continue
        files.append((date, path))
    files.sort(key=lambda x: x[0])

    if not files:
        return {}

    # Build the per-arm chronological list. Missing-arm-in-snapshot is
    # tolerated — just means that arm didn't exist on that day.
    arms: dict[tuple[str, str], list[dict]] = {}
    for _, path in files:
        try:
            with open(path, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    niche_id = row.get("niche_id", "").strip()
                    arm_id = row.get("arm_id", "").strip()
                    if not niche_id or not arm_id:
                        continue
                    key = (niche_id, arm_id)
                    arms.setdefault(key, []).append(row)
        except (OSError, csv.Error) as exc:
            logger.warning("[drift] failed to read %s: %s", path, exc)
            continue

    return arms


def detect_all_drift(
    snapshot_dir: Path | None = None,
    *,
    recent_days: int = _DEFAULT_RECENT_DAYS,
    baseline_days: int = _DEFAULT_BASELINE_DAYS,
    threshold_z: float = _DEFAULT_THRESHOLD_Z,
) -> list[DriftSignal]:
    """Compute drift signals for every arm with a snapshot history.

    Returns the list sorted by ``z_score`` ascending — most-regressed
    arms first, so operators see what needs attention without scrolling.

    Returns ``[]`` on no snapshots / no qualifying arms.
    """
    target_dir = snapshot_dir if snapshot_dir is not None else _snapshot_dir()
    arms = load_snapshots(target_dir, lookback_days=baseline_days)
    if not arms:
        logger.info("[drift] no snapshot data found in %s", target_dir)
        return []

    signals: list[DriftSignal] = []
    for (niche_id, arm_id), history in arms.items():
        sig = compute_arm_drift(
            arm_id=arm_id,
            niche_id=niche_id,
            snapshots=history,
            recent_days=recent_days,
            baseline_days=baseline_days,
        )
        if sig is None:
            continue
        if abs(sig.z_score) >= threshold_z:
            signals.append(sig)

    # Sort by z-score ascending so the most-regressed arms (negative z)
    # appear first.
    signals.sort(key=lambda s: s.z_score)

    # L7 (2026-07-08 audit): persist to Postgres for trend analysis.
    # Guarded by its own flag so `detect_all_drift`'s in-memory return
    # value is unchanged for callers that don't want the DB round-trip.
    # Fail-open — a persistence failure never masks the signal list.
    try:
        from genlab_core.learning.drift_persist import record_signals

        record_signals(signals)
    except Exception as exc:
        # `record_signals` itself never raises (documented contract),
        # but defense in depth against a future accidental regression.
        logger.warning(
            "[drift] persist hook failed: %s (signals still returned)",
            exc,
        )

    return signals


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Bandit arm drift detector.")
    parser.add_argument("--snapshot-dir", type=Path, default=None)
    parser.add_argument("--recent-days", type=int, default=_DEFAULT_RECENT_DAYS)
    parser.add_argument("--baseline-days", type=int, default=_DEFAULT_BASELINE_DAYS)
    parser.add_argument("--threshold-z", type=float, default=_DEFAULT_THRESHOLD_Z)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    signals = detect_all_drift(
        snapshot_dir=args.snapshot_dir,
        recent_days=args.recent_days,
        baseline_days=args.baseline_days,
        threshold_z=args.threshold_z,
    )
    if not signals:
        print("No drift signals above threshold.")
    else:
        print(f"\n{len(signals)} drift signals above |z|>={args.threshold_z}:")
        print(
            f"{'NICHE':<14} {'ARM':<40} "
            f"{'RECENT':>8} {'BASELINE':>10} {'Z':>8} {'REC_N':>7} {'BASE_N':>8}"
        )
        for s in signals:
            marker = "↓" if s.is_regression else "↑"
            print(
                f"{s.niche_id:<14} {s.arm_id[:40]:<40} "
                f"{s.recent_rate:>8.3f} {s.baseline_rate:>10.3f} "
                f"{s.z_score:>+8.2f}{marker} {s.recent_obs:>6d} {s.baseline_obs:>8d}"
            )
