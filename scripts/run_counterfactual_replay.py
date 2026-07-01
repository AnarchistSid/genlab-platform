#!/usr/bin/env python3
"""Counterfactual replay runner (Intervention 7, 2026-07-01).

Companion to ``scripts/ips_replay.py`` — computes IPS AND the new
doubly-robust (DR) estimator per arm, persists the result JSON to
``.tmp/counterfactual-replay/`` for the Strategist / dashboard to
consume, and prints a table summary to stdout.

DR only fires when ``GENLAB_COUNTERFACTUAL_REPLAY_ENABLED=true`` AND
enough rows carry both reward + LinUCB context to fit the reward
model. Otherwise DR fields are None and the run degrades gracefully
to IPS-only (same behaviour as the historical stub).

Design decision — no new Postgres table
=======================================

The natural place to persist DR results would be a
``counterfactual_replay_reports`` table, symmetric with
``strategist_reports``. This runner intentionally does NOT create one:

* DR results are re-derivable at any time from ``pending_feedback``
  (the underlying source of truth). Storing them buys latency, not
  correctness — and the monthly cadence makes latency a non-issue.
* Reader plumbing (Strategist → DR read, dashboard → DR read) doesn't
  exist yet. Coupling a new table to a shipping consumer forces both
  to land together; a JSON artifact lets each land at its own pace.
* Rollback surface stays small — deleting a JSON file is safer than
  reverting a data table.

When the first real consumer (Strategist or the dashboard) needs DR
scores, that PR can also introduce the table + migrate this runner
to dual-write. Until then the artifact is the interface.

Usage
-----

::

    # Default: last 30 days, all niches, table stdout + JSON to
    # .tmp/counterfactual-replay/replay-YYYYMMDD-<niche>.json
    python -m scripts.run_counterfactual_replay

    # Single niche, custom window, no persist
    python -m scripts.run_counterfactual_replay --niche gaming \\
        --window-days 60 --no-persist

    # JSON stdout only (for pipeline consumption)
    python -m scripts.run_counterfactual_replay --format json

    # Explicit output dir (default: ``$GENLAB_TMP/counterfactual-replay``)
    python -m scripts.run_counterfactual_replay --output-dir /tmp/dr

Exit codes
----------

* ``0`` — success (empty-data case is valid, exits 0).
* ``2`` — argparse error.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from genlab_core.learning.ips_replay import (
    compute_doubly_robust_per_arm,
    compute_ips_per_arm,
    load_decisions,
)

logger = logging.getLogger(__name__)

_VALID_NICHES = ("all", "ai_creators", "gaming", "sports", "movies", "anime")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Counterfactual replay — IPS + DR per-arm reward estimates. Intervention 7, 2026-07-01."
        ),
    )
    parser.add_argument(
        "--niche",
        choices=_VALID_NICHES,
        default="all",
        help="Niche to filter on. 'all' = cross-niche (default).",
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=30,
        help="Days back from now to include (default: 30). DR benefits "
        "from a longer window than IPS; 30 keeps the Ridge reward model "
        "stable while still adapting to strategy shifts.",
    )
    parser.add_argument(
        "--min-decisions",
        type=int,
        default=10,
        help="Per-arm minimum for a non-None estimate (default: 10).",
    )
    parser.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
        help="Stdout format. JSON is one line per arm.",
    )
    parser.add_argument(
        "--persist",
        dest="persist",
        action="store_true",
        default=True,
        help="Persist the full JSON report to the output dir (default: on).",
    )
    parser.add_argument(
        "--no-persist",
        dest="persist",
        action="store_false",
        help="Skip disk persistence.",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help=(
            "Directory for the persisted JSON report. Defaults to "
            "``$GENLAB_TMP/counterfactual-replay`` or "
            "``.tmp/counterfactual-replay`` if $GENLAB_TMP is unset."
        ),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable DEBUG logging.",
    )
    return parser.parse_args(argv)


def _resolve_output_dir(arg: str | None) -> Path:
    if arg:
        return Path(arg)
    tmp = os.environ.get("GENLAB_TMP")
    root = Path(tmp) if tmp else Path.cwd() / ".tmp"
    return root / "counterfactual-replay"


def _build_report(
    niche: str,
    window_days: int,
    ips_map: dict,
    dr_map: dict,
    n_decisions: int,
) -> dict[str, Any]:
    """Assemble the persist-ready report."""
    arms = sorted(set(ips_map.keys()) | set(dr_map.keys()))
    per_arm = []
    for arm in arms:
        ips = ips_map.get(arm)
        dr = dr_map.get(arm)
        per_arm.append(
            {
                "arm_id": arm,
                "n_decisions": ips.n_decisions if ips else 0,
                "n_with_reward": ips.n_with_reward if ips else 0,
                "ips_reward": ips.ips_reward if ips else None,
                "naive_reward": ips.naive_reward if ips else None,
                "relative_lift": ips.relative_lift if ips else None,
                "ess": ips.effective_sample_size if ips else 0.0,
                "dr_reward": dr.dr_reward if dr else None,
                "dr_direct_method": dr.direct_method if dr else None,
                "dr_ips_correction": dr.ips_correction if dr else None,
                "dr_n_used": dr.n_used if dr else 0,
                "dr_note": dr.note if dr else "",
            }
        )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "niche": niche,
        "window_days": window_days,
        "n_decisions_total": n_decisions,
        "dr_enabled": os.environ.get("GENLAB_COUNTERFACTUAL_REPLAY_ENABLED", "")
        in ("true", "TRUE", "True"),
        "per_arm": per_arm,
    }


def _print_table(report: dict[str, Any]) -> None:
    print(
        f"\nCounterfactual replay — niche={report['niche']} "
        f"window={report['window_days']}d "
        f"n_decisions={report['n_decisions_total']} "
        f"dr_enabled={report['dr_enabled']}\n"
    )
    header = f"{'arm_id':<40} {'n':>4} {'naive':>7} {'ips':>7} {'dr':>7} {'note':<30}"
    print(header)
    print("-" * len(header))
    for row in report["per_arm"]:

        def fmt(x: float | None) -> str:
            return f"{x:.3f}" if x is not None else "  -  "

        print(
            f"{row['arm_id'][:40]:<40} "
            f"{row['n_with_reward']:>4d} "
            f"{fmt(row['naive_reward']):>7} "
            f"{fmt(row['ips_reward']):>7} "
            f"{fmt(row['dr_reward']):>7} "
            f"{row['dr_note'][:30]:<30}"
        )


def _persist_report(report: dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    path = output_dir / f"replay-{stamp}-{report['niche']}.json"
    path.write_text(json.dumps(report, indent=2, default=str))
    return path


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    since = datetime.now(UTC) - timedelta(days=args.window_days)
    niche_arg = None if args.niche == "all" else args.niche
    decisions = load_decisions(niche_id=niche_arg, since=since)
    logger.info(
        "Loaded %d decisions (niche=%s, window=%dd)",
        len(decisions),
        args.niche,
        args.window_days,
    )

    ips_map = compute_ips_per_arm(decisions, min_decisions=args.min_decisions)
    dr_map = compute_doubly_robust_per_arm(decisions, min_decisions=args.min_decisions)

    report = _build_report(
        niche=args.niche,
        window_days=args.window_days,
        ips_map=ips_map,
        dr_map=dr_map,
        n_decisions=len(decisions),
    )

    if args.format == "json":
        print(json.dumps(report, indent=2, default=str))
    else:
        _print_table(report)

    if args.persist:
        output_dir = _resolve_output_dir(args.output_dir)
        path = _persist_report(report, output_dir)
        logger.info("Persisted to %s", path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
