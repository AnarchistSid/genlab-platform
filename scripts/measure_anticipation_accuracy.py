#!/usr/bin/env python3
"""Anticipation-accuracy measurement runner (Session 3b, 2026-07-01).

For each niche, loads the anticipation artifact from ``lookback-days``
ago and re-fetches pytrends interest to see the observed peak. Emits
per-niche Spearman correlation between predicted composite score and
observed peak intensity.

Fired weekly by ``genlab-anticipation-accuracy.timer`` on Mondays at
05:00 UTC — Monday morning so the operator's weekly review consumes
fresh data. 7-day lookback matches the anticipation module's
"predicts 6-24h ahead" window with generous buffer.

Usage
-----

::

    # Default: all 5 niches, 7-day lookback, persist enabled
    python scripts/measure_anticipation_accuracy.py

    # Single niche, custom lookback
    python scripts/measure_anticipation_accuracy.py --niche gaming \\
        --lookback-days 14

Exit codes
----------

* ``0`` — success (empty result / underpowered measurement is valid, exits 0)
* ``2`` — argparse error
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from genlab_core.intel.anticipation_accuracy import (
    AccuracyMeasurement,
    measure_niche_accuracy,
    persist_measurement,
)

logger = logging.getLogger(__name__)

_VALID_NICHES = ("all", "ai_creators", "gaming", "sports", "movies", "anime")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Anticipation-accuracy measurement — Spearman r between "
            "predicted composite_score and observed peak intensity. "
            "Session 3b, 2026-07-01."
        ),
    )
    parser.add_argument(
        "--niche",
        choices=_VALID_NICHES,
        default="all",
        help="Niche to measure. 'all' loops all 5 (default).",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=7,
        help=(
            "How many days back to load the anticipation artifact "
            "(default: 7). Anticipation predicts 6-24h ahead; 7 days "
            "gives the peak time to materialise + fully complete."
        ),
    )
    parser.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
        help="Stdout format.",
    )
    parser.add_argument(
        "--persist",
        dest="persist",
        action="store_true",
        default=True,
        help="Persist per-niche JSON artifacts (default: on).",
    )
    parser.add_argument(
        "--no-persist",
        dest="persist",
        action="store_false",
        help="Skip disk persistence.",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser.parse_args(argv)


def _print_table(rows: list[AccuracyMeasurement]) -> None:
    header = f"{'niche':<14} {'r':>7} {'p':>7} {'N':>3}  reasons"
    print(header)
    print("-" * len(header))
    for m in rows:
        r = f"{m.spearman_r:+.3f}" if m.spearman_r is not None else "  —  "
        p = f"{m.p_value:.3f}" if m.p_value is not None else "  —  "
        reasons_short = "; ".join(m.reasons)[:40]
        print(f"{m.niche_id:<14} {r:>7} {p:>7} {m.n_topics:>3}  {reasons_short}")


def _run_one_niche(
    niche_id: str,
    args: argparse.Namespace,
) -> dict[str, Any] | AccuracyMeasurement:
    measurement = measure_niche_accuracy(
        niche_id,
        lookback_days=args.lookback_days,
    )
    logger.info(
        "Measured %s: r=%s N=%d",
        niche_id,
        measurement.spearman_r,
        measurement.n_topics,
    )
    if args.persist:
        persist_measurement(measurement)
    return measurement


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    niches = (
        ("ai_creators", "gaming", "sports", "movies", "anime")
        if args.niche == "all"
        else (args.niche,)
    )

    results: list[AccuracyMeasurement] = []
    for nid in niches:
        try:
            results.append(_run_one_niche(nid, args))
        except Exception as exc:
            logger.warning("measurement failed for niche=%s: %s", nid, exc)

    if args.format == "json":
        print(
            json.dumps(
                [m.to_json() for m in results],
                indent=2,
                default=str,
            )
        )
    else:
        _print_table(results)

    return 0


if __name__ == "__main__":
    sys.exit(main())
