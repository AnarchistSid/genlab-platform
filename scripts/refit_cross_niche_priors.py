#!/usr/bin/env python3
"""Cross-niche prior refit runner (Intervention 2, 2026-07-01).

Loads bandit_arms across all 5 niches, groups by style suffix, and
computes moment-matched Beta priors per style. Persists to
``$GENLAB_TMP/cross-niche-transfer/priors.json``.

Fires weekly by ``genlab-cross-niche-transfer.timer`` (Mondays 05:30
UTC — 30 minutes after the anticipation-accuracy runner). New arms
created after the refit inherit the transferred prior via
:func:`~genlab_core.learning.cross_niche_transfer.get_transferred_prior`;
already-instantiated arms are unaffected.

Usage
-----

::

    # Default: all 5 niches, persist
    python scripts/refit_cross_niche_priors.py

    # Preview without persisting (debugging)
    python scripts/refit_cross_niche_priors.py --no-persist \\
        --format json

Exit codes
----------

* ``0`` — success (empty result is valid, e.g. all styles below the
  min-observation threshold — exits 0)
* ``2`` — argparse error
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from genlab_core.learning.cross_niche_transfer import (
    compute_transferred_priors,
    persist_priors,
)

logger = logging.getLogger(__name__)

_NICHES = ("ai_creators", "gaming", "sports", "movies", "anime")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=("Cross-niche hierarchical Bayesian prior refit. Intervention 2, 2026-07-01."),
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
        help="Persist priors JSON (default: on).",
    )
    parser.add_argument(
        "--no-persist",
        dest="persist",
        action="store_false",
        help="Skip disk persistence (debugging).",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser.parse_args(argv)


def _load_all_niche_arms() -> dict[str, dict[str, tuple[float, float]]]:
    """Load ``{niche: {arm_id: (alpha, beta)}}`` for all 5 niches
    via the shared arm_loader. Missing niches / DB errors → empty
    dict for that niche (the compute function drops them
    naturally)."""
    from genlab_core.http.backlog_client import BacklogClient
    from genlab_core.learning.arm_loader import load_all_arms

    client = BacklogClient()
    proxy = getattr(client, "bandit_arms", None)
    if proxy is None:
        logger.warning("BacklogClient has no bandit_arms proxy — nothing to refit")
        return {}

    out: dict[str, dict[str, tuple[float, float]]] = {}
    for niche_id in _NICHES:
        try:
            out[niche_id] = load_all_arms(proxy, niche_id)
        except Exception as exc:
            logger.warning("load_all_arms failed for %s: %s", niche_id, exc)
            out[niche_id] = {}
    return out


def _print_table(priors: dict) -> None:
    if not priors:
        print("No transferable priors — no style met the min-observation threshold")
        return
    header = f"{'style':<20} {'α':>7} {'β':>7} {'μ':>7} {'σ²':>7} {'N':>3}"
    print(header)
    print("-" * len(header))
    for style, p in sorted(priors.items()):
        print(
            f"{style:<20} {p.alpha:>7.2f} {p.beta:>7.2f} "
            f"{p.observed_rate_mean:>7.3f} {p.observed_rate_variance:>7.4f} "
            f"{p.n_niches:>3}"
        )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    bandit_state = _load_all_niche_arms()
    priors = compute_transferred_priors(bandit_state)

    logger.info(
        "Cross-niche refit: %d styles produced priors from %d niches with data",
        len(priors),
        sum(1 for arms in bandit_state.values() if arms),
    )

    if args.format == "json":
        payload: dict[str, Any] = {
            "n_styles": len(priors),
            "priors": {style: p.to_json() for style, p in priors.items()},
        }
        print(json.dumps(payload, indent=2, default=str))
    else:
        _print_table(priors)

    if args.persist:
        path = persist_priors(priors)
        logger.info("Persisted to %s", path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
