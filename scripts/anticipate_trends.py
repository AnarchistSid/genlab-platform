#!/usr/bin/env python3
"""Trend Anticipation daily runner (Intervention 5 Session 1, 2026-07-01).

Fetches the currently-trending topics per niche via
``GoogleTrendsIntel.get_trending_topics()``, ranks them by
anticipation score
(:func:`genlab_core.intel.trend_anticipation.rank_topics`), persists
the ranked list to ``$GENLAB_TMP/trend-anticipation/YYYYMMDD-<niche>.json``.

Fires from the ``genlab-anticipate-trends.timer`` systemd unit daily
at 03:30 UTC — after the late-reward 04:00 UTC run and BEFORE the
first per-niche pipeline runs (which start 06:30 UTC and later). This
timing lets the pipeline read today's artifact when it constructs
its candidate list.

Usage
-----

::

    # Default: all 5 niches, top-5 per niche, persist enabled
    python scripts/anticipate_trends.py

    # Single niche, custom top-N
    python scripts/anticipate_trends.py --niche gaming --top-n 10

    # JSON stdout only (no persist) for debugging
    python scripts/anticipate_trends.py --niche ai_creators --no-persist \\
        --format json

Exit codes
----------

* ``0`` — success (empty-ranking case is valid, exits 0)
* ``2`` — argparse error
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from genlab_core.intel.google_trends import GoogleTrendsIntel
from genlab_core.intel.trend_anticipation import (
    AnticipationScore,
    persist_ranking,
    rank_topics,
)

logger = logging.getLogger(__name__)

_VALID_NICHES = ("all", "ai_creators", "gaming", "sports", "movies", "anime")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Trend anticipation runner — daily rank of upcoming topics per niche. "
            "Intervention 5 Session 1, 2026-07-01."
        ),
    )
    parser.add_argument(
        "--niche",
        choices=_VALID_NICHES,
        default="all",
        help="Niche to score. 'all' loops through the 5 shipped niches (default).",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=5,
        help="How many top-scored topics to persist per niche (default: 5).",
    )
    parser.add_argument(
        "--candidates-per-niche",
        type=int,
        default=15,
        help=(
            "How many currently-trending topics to fetch per niche BEFORE "
            "anticipation ranking. The runner picks top-N from these by "
            "anticipation score (default: 15)."
        ),
    )
    parser.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
        help="Stdout format. Table for humans, JSON for pipeline consumption.",
    )
    parser.add_argument(
        "--persist",
        dest="persist",
        action="store_true",
        default=True,
        help="Persist JSON artifact to the output dir (default: on).",
    )
    parser.add_argument(
        "--no-persist",
        dest="persist",
        action="store_false",
        help="Skip artifact persistence (debugging).",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable DEBUG logging.",
    )
    return parser.parse_args(argv)


def _print_table(niche_id: str, ranking: list[AnticipationScore]) -> None:
    print(f"\n── niche={niche_id}, top {len(ranking)} ──")
    header = f"{'#':>2} {'composite':>9} {'conf':>4} {'ETA':>4}  topic"
    print(header)
    print("-" * len(header))
    for i, score in enumerate(ranking, 1):
        eta = (
            f"{score.anticipated_peak_hours_ahead:+d}h"
            if score.anticipated_peak_hours_ahead is not None
            else " —  "
        )
        print(
            f"{i:>2} {score.composite_score:>9.3f} "
            f"{score.confidence:>4.2f} {eta:>4}  {score.topic[:50]}"
        )


def _run_one_niche(
    niche_id: str,
    intel: GoogleTrendsIntel,
    args: argparse.Namespace,
) -> dict[str, Any]:
    topics = intel.get_trending_topics(niche_id, top_n=args.candidates_per_niche)
    logger.info(
        "Fetched %d candidate topics for niche=%s",
        len(topics),
        niche_id,
    )
    ranking = rank_topics(topics, niche_id, top_n=args.top_n)

    if args.format == "table":
        _print_table(niche_id, ranking)
    else:
        print(
            json.dumps(
                {
                    "niche_id": niche_id,
                    "candidates": len(topics),
                    "ranking": [s.to_json() for s in ranking],
                },
                indent=2,
                default=str,
            )
        )

    persisted_path: str | None = None
    if args.persist:
        path = persist_ranking(niche_id, ranking)
        persisted_path = str(path)
        logger.info("Persisted ranking to %s", path)

    return {
        "niche_id": niche_id,
        "n_candidates": len(topics),
        "n_ranked": len(ranking),
        "persisted": persisted_path,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    intel = GoogleTrendsIntel()

    niches = (
        ("ai_creators", "gaming", "sports", "movies", "anime")
        if args.niche == "all"
        else (args.niche,)
    )

    summary = []
    for nid in niches:
        try:
            summary.append(_run_one_niche(nid, intel, args))
        except Exception as exc:
            # A single-niche failure must NOT abort the run — the
            # other niches still produce useful artifacts.
            logger.warning("Anticipation run failed for niche=%s: %s", nid, exc)
            summary.append({"niche_id": nid, "error": str(exc)})

    if args.format == "table" or args.verbose:
        n_ok = sum(1 for s in summary if "error" not in s)
        print(f"\n── summary: {n_ok}/{len(summary)} niches processed ──")
    return 0


if __name__ == "__main__":
    sys.exit(main())
