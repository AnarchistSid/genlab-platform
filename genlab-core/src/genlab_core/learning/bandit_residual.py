"""Bandit residual diagnostic (AUTO #2 runbook D2.5).

Surfaces bandit arms whose observed-reward average diverges from the
posterior mean — i.e. arms where the prior is dragging the posterior
away from what the empirical observations support.

## Math

For a Beta(α, β) Thompson-Sampling arm with prior α₀=1, β₀=1
(see ``metric_collector._default_bandit_updater``):

* ``observed_total_reward = α - α₀ = α - 1``
* ``observed_avg = observed_total_reward / n_plays``
* ``posterior_mean = α / (α + β)``
* ``residual = observed_avg - posterior_mean``

The residual is the gap between what observations alone would say
about the arm's mean and what the posterior — diluted by the prior
— actually says. As ``n_plays → ∞`` the residual → 0 because the
prior's influence decays as 2/(α+β). On under-explored arms
(``n_plays`` < ~10) the residual can be substantial even when the
arm is performing exactly as the empirical data implies.

Use cases:

* **Large +residual** — empirical reward is higher than the
  posterior suggests. Prior is too pessimistic. Bandit will
  under-explore this arm relative to its true performance.
* **Large -residual** — empirical reward is lower than the
  posterior suggests. Prior is too optimistic (which is the case
  for Beta(1,1) on any arm where observed_avg < 0.5). Bandit
  will over-explore.
* **|residual| × log(n_plays) ranking** — emphasises well-explored
  arms over noisy ones; surfaces calibration drift over noise.

Output: a list of ``ArmResidual`` dataclasses with ``to_csv`` /
``to_dict`` helpers; CLI prints top-N misaligned arms + writes a
full CSV.

This module is intentionally read-only — it never mutates
``bandit_arms``. Use ``arm_loader.save_arm`` for that.
"""

from __future__ import annotations

import argparse
import csv
import logging
import math
import os
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Match metric_collector's defaults. If the priors ever change, both
# call sites must move together — see _default_bandit_updater.
_PRIOR_ALPHA = 1.0
_PRIOR_BETA = 1.0

# Arms with fewer plays are treated as "still cold-starting" — residual
# is informative but noisy. Surface them via a separate flag rather
# than mixed in with the calibration-drift cases.
_MIN_PLAYS_FOR_DRIFT = 5

# A residual above this magnitude on a warm arm is "interesting" —
# worth surfacing in the top-N output. Tune via CLI ``--threshold``.
_DEFAULT_DRIFT_THRESHOLD = 0.15


@dataclass(frozen=True)
class ArmResidual:
    """One row of the residual report.

    All numeric fields are rounded for human readability when written
    via ``to_csv``; the raw values stay available on the dataclass.
    """

    niche_id: str
    arm_id: str
    n_plays: int
    alpha: float
    beta: float
    observed_avg: float | None  # None when n_plays==0 (no observations)
    posterior_mean: float
    residual: float | None
    is_cold_start: bool  # True when n_plays < _MIN_PLAYS_FOR_DRIFT
    is_drift_flagged: bool  # True when |residual| > threshold AND not cold-start
    ranking_score: float  # |residual| * log(1 + n_plays) for sorting

    def to_dict(self) -> dict:
        return asdict(self)


def _compute_residual(
    *,
    alpha: float,
    beta: float,
    n_plays: int,
    drift_threshold: float = _DEFAULT_DRIFT_THRESHOLD,
) -> tuple[float | None, float | None, bool, bool, float]:
    """Pure math — broken out so the unit tests don't need a DB.

    Returns:
        observed_avg, residual, is_cold_start, is_drift_flagged,
        ranking_score

    None for observed_avg/residual when n_plays == 0 (no observations
    at all — residual is mathematically undefined).
    """
    posterior_mean = alpha / (alpha + beta) if (alpha + beta) > 0 else 0.0

    if n_plays <= 0:
        # No observations — only the prior. We can still surface
        # posterior_mean but residual makes no sense.
        return None, None, True, False, 0.0

    observed_avg = (alpha - _PRIOR_ALPHA) / n_plays
    # Clamp pathological cases (observed_avg should be in [0, 1] given
    # the clip in metric_collector but float drift can push it slightly
    # negative on freshly-bumped low-reward arms).
    observed_avg = max(0.0, min(1.0, observed_avg))

    residual = observed_avg - posterior_mean
    is_cold_start = n_plays < _MIN_PLAYS_FOR_DRIFT
    is_drift_flagged = (not is_cold_start) and abs(residual) > drift_threshold
    ranking_score = abs(residual) * math.log1p(n_plays)

    return observed_avg, residual, is_cold_start, is_drift_flagged, ranking_score


def compute_residuals(
    rows: list[dict],
    *,
    drift_threshold: float = _DEFAULT_DRIFT_THRESHOLD,
) -> list[ArmResidual]:
    """Compute ArmResidual records from raw bandit_arms rows.

    Args:
        rows: list of dicts shaped like the bandit_arms table (must
            have niche_id, arm_id, alpha, beta, n_plays).
        drift_threshold: |residual| above this on a warm arm gets
            ``is_drift_flagged = True``.

    Returns:
        ArmResidual list sorted DESC by ranking_score (most-misaligned
        first), with stable secondary ordering by (niche_id, arm_id).
    """
    out: list[ArmResidual] = []
    for r in rows:
        alpha = float(r.get("alpha") or _PRIOR_ALPHA)
        beta = float(r.get("beta") or _PRIOR_BETA)
        n_plays = int(r.get("n_plays") or 0)
        posterior_mean = alpha / (alpha + beta) if (alpha + beta) > 0 else 0.0

        observed_avg, residual, is_cold_start, is_drift_flagged, ranking_score = (
            _compute_residual(
                alpha=alpha,
                beta=beta,
                n_plays=n_plays,
                drift_threshold=drift_threshold,
            )
        )
        out.append(
            ArmResidual(
                niche_id=str(r.get("niche_id", "")),
                arm_id=str(r.get("arm_id", "")),
                n_plays=n_plays,
                alpha=alpha,
                beta=beta,
                observed_avg=observed_avg,
                posterior_mean=posterior_mean,
                residual=residual,
                is_cold_start=is_cold_start,
                is_drift_flagged=is_drift_flagged,
                ranking_score=ranking_score,
            )
        )

    out.sort(key=lambda a: (-a.ranking_score, a.niche_id, a.arm_id))
    return out


def write_csv(rows: list[ArmResidual], path: Path) -> None:
    """Write the full residual list to a CSV file.

    Float columns are rounded to 4 decimals for human-readable diffs.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "niche_id",
                "arm_id",
                "n_plays",
                "alpha",
                "beta",
                "observed_avg",
                "posterior_mean",
                "residual",
                "is_cold_start",
                "is_drift_flagged",
                "ranking_score",
            ]
        )
        for r in rows:
            writer.writerow(
                [
                    r.niche_id,
                    r.arm_id,
                    r.n_plays,
                    round(r.alpha, 4),
                    round(r.beta, 4),
                    "" if r.observed_avg is None else round(r.observed_avg, 4),
                    round(r.posterior_mean, 4),
                    "" if r.residual is None else round(r.residual, 4),
                    int(r.is_cold_start),
                    int(r.is_drift_flagged),
                    round(r.ranking_score, 4),
                ]
            )


def _query_bandit_arms() -> list[dict]:
    """Pull bandit_arms rows from Postgres for the diagnostic.

    Returns an empty list (with a logger.warning) when DATABASE_URL is
    unset — the CLI prints a clear error rather than crashing.
    """
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        logger.warning("[bandit_residual] DATABASE_URL not set")
        return []

    try:
        import psycopg
    except ImportError:
        logger.warning("[bandit_residual] psycopg not installed")
        return []

    rows: list[dict] = []
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        # Bypass RLS so the diagnostic sees every niche in one pass.
        # bandit_arms is global state anyway — niches don't have their
        # own arms isolated from each other.
        with conn.cursor() as cur:
            cur.execute("SET app.niche_id = ''")
            cur.execute(
                """
                SELECT niche_id, arm_id, alpha, beta, n_plays
                FROM bandit_arms
                ORDER BY niche_id, arm_id
                """
            )
            for niche_id, arm_id, alpha, beta, n_plays in cur.fetchall():
                rows.append(
                    {
                        "niche_id": niche_id,
                        "arm_id": arm_id,
                        "alpha": float(alpha) if alpha is not None else _PRIOR_ALPHA,
                        "beta": float(beta) if beta is not None else _PRIOR_BETA,
                        "n_plays": int(n_plays or 0),
                    }
                )
    return rows


def _format_top_n_table(residuals: list[ArmResidual], n: int) -> str:
    """Pretty-print the top-N misaligned arms for stdout."""
    drift = [r for r in residuals if r.is_drift_flagged][:n]
    if not drift:
        return "No drift-flagged arms (every warm arm within threshold)."

    lines = [
        f"{'niche_id':<14} {'arm_id':<34} {'plays':>5} "
        f"{'obs_avg':>8} {'post_mean':>9} {'residual':>9}"
    ]
    for r in drift:
        lines.append(
            f"{r.niche_id:<14} {r.arm_id[:34]:<34} {r.n_plays:>5} "
            f"{(r.observed_avg or 0):>8.3f} {r.posterior_mean:>9.3f} "
            f"{(r.residual or 0):>+9.3f}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI: `python -m genlab_core.learning.bandit_residual`."""
    parser = argparse.ArgumentParser(
        prog="bandit_residual",
        description="Diagnostic report for bandit posterior drift.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".tmp/bandit_residual.csv"),
        help="Path to write the full CSV report.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=_DEFAULT_DRIFT_THRESHOLD,
        help="|residual| above this on a warm arm is flagged (default 0.15).",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="How many drift-flagged arms to print to stdout (default 10).",
    )
    args = parser.parse_args(argv)

    rows = _query_bandit_arms()
    if not rows:
        print("No bandit_arms rows found (DATABASE_URL set?)", file=sys.stderr)
        return 1

    residuals = compute_residuals(rows, drift_threshold=args.threshold)
    write_csv(residuals, args.output)

    print(
        f"# bandit_residual report — {datetime.now(UTC).isoformat()}",
        f"# {len(rows)} arms inspected, "
        f"{sum(1 for r in residuals if r.is_drift_flagged)} drift-flagged, "
        f"{sum(1 for r in residuals if r.is_cold_start)} cold-start",
        f"# full csv: {args.output}",
        "",
        f"## TOP-{args.top} DRIFT-FLAGGED ARMS (by |residual| * log(1+n_plays))",
        _format_top_n_table(residuals, args.top),
        sep="\n",
    )
    return 0


if __name__ == "__main__":  # pragma: no cover — entry point
    sys.exit(main())
