#!/usr/bin/env python3
"""IPS counterfactual replay CLI — preview per-arm reward estimates.

Reads ``pending_feedback`` rows with ``propensity IS NOT NULL`` (PR
#634 / #636 producer) and computes the Inverse Propensity Score reward
estimate per bandit arm. Pure read-side analysis tool — never mutates.

What this answers
-----------------

"What would the average reward have been if we'd assigned arms with
weight 1/p instead of just averaging observed reward by arm?"

When the system is in deterministic mode (``GENLAB_LINUCB_STOCHASTIC_
ENABLED=0``), every recorded propensity is 1.0 and IPS reduces exactly
to the naive mean. The CLI displays both side-by-side so the degeneracy
is obvious.

Usage
-----

::

    # Default: last 7 days, all niches, table format
    python -m scripts.ips_replay

    # Single niche, custom window
    python -m scripts.ips_replay --niche ai_creators --window-days 30

    # Since a specific date
    python -m scripts.ips_replay --niche gaming --since 2026-06-29

    # JSON output for downstream tooling
    python -m scripts.ips_replay --format json

    # Tighter minimum sample size
    python -m scripts.ips_replay --min-decisions 25

Exit codes
----------

* ``0`` — success (including the "no data" case — empty data is a
  valid result, not an error).
* ``2`` — argument parse error (argparse default).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

from genlab_core.learning.ips_replay import (
    IPSEstimate,
    compute_ips_per_arm,
    load_decisions,
)

logger = logging.getLogger(__name__)

_VALID_NICHES = ("all", "ai_creators", "gaming", "sports", "movies", "anime")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "IPS counterfactual replay — per-arm reward estimates from "
            "pending_feedback.propensity (PR #634/#636 consumer)."
        ),
    )
    parser.add_argument(
        "--niche",
        choices=_VALID_NICHES,
        default="all",
        help="Niche to filter on. 'all' returns cross-niche replay (default).",
    )
    parser.add_argument(
        "--since",
        type=str,
        default=None,
        help="ISO date (YYYY-MM-DD) lower bound. Overrides --window-days when set.",
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=7,
        help="Lookback window in days. Ignored when --since is set. Default 7.",
    )
    parser.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
        help="Output format. Default table.",
    )
    parser.add_argument(
        "--min-decisions",
        type=int,
        default=10,
        help=(
            "Minimum decisions-with-reward per arm before an IPS estimate "
            "is computed. Below this, the arm is shown with n_decisions / "
            "n_with_reward only. Default 10."
        ),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="DEBUG logging.",
    )
    return parser.parse_args(argv)


def _resolve_since(args: argparse.Namespace) -> datetime:
    """Resolve --since OR --window-days to a single timezone-aware datetime.

    --since takes precedence when set. Always returns UTC.
    """
    if args.since:
        try:
            d = datetime.fromisoformat(args.since)
        except ValueError as exc:
            raise SystemExit(f"--since not parseable as ISO date: {exc}") from exc
        if d.tzinfo is None:
            d = d.replace(tzinfo=UTC)
        return d
    return datetime.now(UTC) - timedelta(days=args.window_days)


def _format_table(
    estimates: dict[str, IPSEstimate],
    *,
    niche_label: str,
    since: datetime,
    total_decisions: int,
    total_with_reward: int,
) -> str:
    """Render the human-readable table format.

    Sort order: arms with computable IPS first (sorted by IPS desc),
    then arms below min_decisions (sorted by n_decisions desc). This
    surfaces the operator's most-actionable information first.
    """
    header = f"IPS replay — niche={niche_label}, since={since.strftime('%Y-%m-%d %H:%MZ')}\n"
    sep = "─" * 75 + "\n"

    if not estimates:
        return (
            header
            + sep
            + "no decisions with logged propensity in window — flip on "
            + "GENLAB_LINUCB_STOCHASTIC_ENABLED=1 and re-run after "
            + "decisions accumulate.\n"
            + sep
        )

    measurable = sorted(
        (e for e in estimates.values() if e.ips_reward is not None),
        key=lambda e: -(e.ips_reward or 0.0),
    )
    coverage_only = sorted(
        (e for e in estimates.values() if e.ips_reward is None),
        key=lambda e: -e.n_decisions,
    )

    col_header = (
        f"{'arm_id':<30s} {'n_dec':>6s} {'n_rew':>6s} "
        f"{'naive':>7s} {'IPS':>7s} {'Δ%':>7s} {'ESS':>7s}\n"
    )
    out = header + sep + col_header + sep

    for e in measurable:
        # type checker: when in measurable, IPS / naive / weights are set.
        ips = e.ips_reward or 0.0
        naive = e.naive_reward or 0.0
        lift_str = f"{e.relative_lift * 100:+.1f}%" if e.relative_lift is not None else "    —"
        out += (
            f"{e.arm_id[:30]:<30s} "
            f"{e.n_decisions:>6d} "
            f"{e.n_with_reward:>6d} "
            f"{naive:>7.3f} "
            f"{ips:>7.3f} "
            f"{lift_str:>7s} "
            f"{e.effective_sample_size:>7.1f}\n"
        )

    if coverage_only:
        out += sep
        out += "(below min-decisions threshold — coverage only)\n"
        for e in coverage_only:
            out += (
                f"{e.arm_id[:30]:<30s} "
                f"{e.n_decisions:>6d} "
                f"{e.n_with_reward:>6d} "
                f"{'—':>7s} {'—':>7s} {'—':>7s} {'—':>7s}\n"
            )

    out += sep
    out += (
        f"N = {total_with_reward} decisions with reward (of {total_decisions} total)\n"
        f"ESS warning threshold ≈ N/2 = {total_with_reward / 2:.1f} "
        "(per-arm ESS below this = high IPS variance)\n"
    )
    return out


def _format_json(
    estimates: dict[str, IPSEstimate],
    *,
    niche_label: str,
    since: datetime,
    total_decisions: int,
    total_with_reward: int,
) -> str:
    """Render JSON for downstream tooling.

    Excludes the ``weights`` array (potentially large) — callers who
    want it should import ``compute_ips_per_arm`` directly.
    """
    payload: dict[str, Any] = {
        "niche": niche_label,
        "since": since.isoformat(),
        "total_decisions": total_decisions,
        "total_with_reward": total_with_reward,
        "estimates": [
            {
                "arm_id": e.arm_id,
                "n_decisions": e.n_decisions,
                "n_with_reward": e.n_with_reward,
                "ips_reward": e.ips_reward,
                "naive_reward": e.naive_reward,
                "relative_lift": e.relative_lift,
                "effective_sample_size": e.effective_sample_size,
            }
            for e in sorted(
                estimates.values(),
                key=lambda x: -(x.ips_reward if x.ips_reward is not None else -1.0),
            )
        ],
    }
    return json.dumps(payload, indent=2, default=str)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    since = _resolve_since(args)
    niche_filter = None if args.niche == "all" else args.niche

    decisions = load_decisions(niche_id=niche_filter, since=since)

    estimates = compute_ips_per_arm(
        decisions,
        min_decisions=args.min_decisions,
    )

    total_decisions = len(decisions)
    total_with_reward = sum(1 for d in decisions if d.reward is not None)

    if args.format == "json":
        sys.stdout.write(
            _format_json(
                estimates,
                niche_label=args.niche,
                since=since,
                total_decisions=total_decisions,
                total_with_reward=total_with_reward,
            )
            + "\n"
        )
    else:
        sys.stdout.write(
            _format_table(
                estimates,
                niche_label=args.niche,
                since=since,
                total_decisions=total_decisions,
                total_with_reward=total_with_reward,
            )
        )

    # Empty data is a valid result (no propensity logged yet, etc.) —
    # exit 0 so cron / smoke runs don't alarm. Argparse errors already
    # exit 2 via SystemExit before we reach here.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
