#!/usr/bin/env python3
"""Weekly top-creator prior refit (B.2, 2026-07-08).

Fetches each niche's top-performing videos via YouTube's
``videos.list(chart=mostPopular)`` API, extracts structural features
via B.1's ``top_creator_features``, computes Spearman correlations
between each feature and view velocity, and writes a per-niche
``top_creator_priors.json`` artifact.

## Why weekly and not daily

Feature-view correlations don't shift meaningfully day-to-day — the
underlying signal is "does question-format vs listicle-format
outperform on gaming YouTube this week?" which is measured across
tens of thousands of views accumulated over days. Weekly refit gives
the runner enough distributional stability while still capturing
seasonal shifts.

## Quota cost

Per fire:
* 4 niches with categories (gaming, sports, movies, ai_creators) x
  1 unit each for ``videos.list(chart=mostPopular)`` = 4 units.
* ``anime`` has no YouTube category — skipped (fall back to
  keyword search would cost 100 quota; not worth it for weekly refit).
* Weekly: 4 units/week = ~17 units/month.

Rounds to ~0.2% of the daily 10K quota per fire. Never worth
budget-worrying about.

## Artifact shape

Per-niche JSON written at ``$GENLAB_TMP/top-creator-priors/
YYYYMMDD-<niche>.json``:

    {
      "generated_at": "2026-07-13T04:30:00+00:00",
      "niche_id": "gaming",
      "video_count": 24,
      "correlations": {
        "title_length_chars": -0.15,
        "title_ends_with_question": 0.31,
        "title_starts_with_number": 0.08,
        "title_number_count": 0.06,
        "title_emoji_count": -0.04,
        "title_allcaps_word_count": 0.12,
        "description_length_chars": 0.19,
        "description_first_line_length": 0.22,
        "description_has_timestamps": 0.28,
        "tags_count": 0.11,
        "publish_hour_utc": -0.03,
        "publish_day_of_week": 0.09
      }
    }

## Guardrails

* ``GENLAB_TOP_CREATOR_PRIORS_ENABLED`` env flag — off by default.
  Runner is a no-op unless flipped, matching the discipline of A.2
  and every other intelligence engine.
* Missing ``YOUTUBE_API_KEY`` → exit 1 with WARN log.
* < 3 videos returned per niche → skip niche (correlation undefined).
* Any single niche fetch failure → log + continue with the rest.

## Consumer

B.3's ``genlab_core.learning.top_creator_priors.load_correlations``
reads today's / yesterday's artifact and exposes per-feature
correlation for cold-start prior adjustments at arm registration.

## Exit codes

    0 — success (or no-op when flag off)
    1 — YOUTUBE_API_KEY missing
    2 — every niche fetch failed
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("refit_top_creator_priors")


# Niches with YouTube video categories. Anime is intentionally not
# here — no native YT category and the fallback keyword search costs
# 100 quota units per call which we don't want to burn on a weekly
# refit.
_NICHES_WITH_CATEGORY: dict[str, str] = {
    "gaming": "20",
    "sports": "17",
    "movies": "1",
    "ai_creators": "28",
}

# The refit is intentionally region-locked to US. Cross-region
# comparison is out of scope — we're measuring structural feature
# correlations, not tuning localised content.
_REGION_CODE = "US"

# YouTube caps videos.list(chart=mostPopular) at 50 per response.
# 50 is enough sample size for Spearman correlation to stabilise;
# there's no paginate-and-merge that would help the signal.
_MAX_VIDEOS = 50


def _flag_enabled() -> bool:
    """Exact-match ``true`` per the intelligence-package convention.

    Same pattern as GENLAB_TREND_ANTICIPATION_ENABLED,
    GENLAB_CROSS_NICHE_TRANSFER_ENABLED, and every other Intervention
    runner's flag. `"1"` / `"yes"` / `"TRUE"` intentionally do NOT
    match (docstring convention documented in flag consumers).
    """
    return os.environ.get("GENLAB_TOP_CREATOR_PRIORS_ENABLED", "").strip() == "true"


def _artifact_dir() -> Path:
    tmp = os.environ.get("GENLAB_TMP")
    root = Path(tmp) if tmp else Path.cwd() / ".tmp"
    return root / "top-creator-priors"


def _fetch_mostpopular(
    api_key: str,
    category_id: str,
    max_results: int = _MAX_VIDEOS,
) -> list[dict[str, Any]]:
    """Fetch YouTube's mostPopular chart for a category.

    Returns the raw ``items`` list from the API response. Each item
    has the ``snippet`` + ``statistics`` sub-dicts that B.1's feature
    extractor accepts natively.

    Returns empty list on any API error (fail-open — a bad response
    from one category shouldn't tank the whole refit).
    """
    try:
        import requests

        resp = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={
                "part": "snippet,statistics",
                "chart": "mostPopular",
                "regionCode": _REGION_CODE,
                "videoCategoryId": category_id,
                "maxResults": max_results,
                "key": api_key,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            logger.warning(
                "[refit-priors] videos.list(cat=%s) HTTP %d",
                category_id,
                resp.status_code,
            )
            return []
        items = resp.json().get("items") or []
        return items
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[refit-priors] videos.list(cat=%s) exception: %s",
            category_id,
            exc,
        )
        return []


def _write_artifact(
    dir_: Path,
    niche_id: str,
    video_count: int,
    correlations: dict[str, float],
) -> Path:
    """Write per-niche artifact. Idempotent within a UTC day —
    running twice on the same day overwrites yesterday's version."""
    dir_.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    path = dir_ / f"{stamp}-{niche_id}.json"
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "niche_id": niche_id,
        "video_count": video_count,
        "correlations": correlations,
    }
    path.write_text(json.dumps(payload, indent=2))
    return path


def run(
    api_key: str,
    niches: dict[str, str] | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """Fetch mostPopular per niche and write correlation artifacts.

    Returns stats for the CLI summary. Split from ``main()`` so
    tests can drive the flow without touching sys.exit.

    Args:
        api_key: YouTube Data API v3 key.
        niches: Override the niche→category map (tests use this).
            Defaults to ``_NICHES_WITH_CATEGORY``.
        dry_run: If True, compute correlations but don't write.
    """
    from genlab_core.intel.top_creator_features import (
        compute_feature_view_correlation,
    )

    if niches is None:
        niches = _NICHES_WITH_CATEGORY

    dir_ = _artifact_dir()
    stats = {
        "niches_processed": 0,
        "niches_skipped_low_volume": 0,
        "niches_failed": 0,
        "videos_fetched": 0,
    }

    for niche_id, category_id in niches.items():
        items = _fetch_mostpopular(api_key, category_id)
        if not items:
            logger.warning("[refit-priors] %s: fetch returned 0 items", niche_id)
            stats["niches_failed"] += 1
            continue

        stats["videos_fetched"] += len(items)

        # B.1's correlation function needs >= 3 videos to compute
        # meaningful Spearman. YouTube's mostPopular returns 50 in
        # steady state, so this only trips on regional edge cases.
        if len(items) < 3:
            logger.info(
                "[refit-priors] %s: %d videos < 3 threshold — skipping",
                niche_id,
                len(items),
            )
            stats["niches_skipped_low_volume"] += 1
            continue

        correlations = compute_feature_view_correlation(items)
        if not correlations:
            logger.info(
                "[refit-priors] %s: correlation function returned empty — skipping",
                niche_id,
            )
            stats["niches_skipped_low_volume"] += 1
            continue

        if dry_run:
            logger.info(
                "[DRY-RUN] would write %s (%d videos, %d features)",
                f"{niche_id}.json",
                len(items),
                len(correlations),
            )
        else:
            path = _write_artifact(dir_, niche_id, len(items), correlations)
            logger.info(
                "[refit-priors] wrote %s (%d videos, %d features)",
                path.name,
                len(items),
                len(correlations),
            )
        stats["niches_processed"] += 1

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch + compute but don't write artifacts.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable DEBUG logging.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if not _flag_enabled():
        logger.info("GENLAB_TOP_CREATOR_PRIORS_ENABLED not set — no-op. Flip flag to activate.")
        return 0

    api_key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not api_key:
        logger.error("YOUTUBE_API_KEY not set")
        return 1

    logger.info("Refitting priors across %d niches", len(_NICHES_WITH_CATEGORY))
    stats = run(api_key, dry_run=args.dry_run)

    logger.info(
        "Summary: %d processed, %d skipped (low volume), %d failed, %d videos fetched",
        stats["niches_processed"],
        stats["niches_skipped_low_volume"],
        stats["niches_failed"],
        stats["videos_fetched"],
    )

    # Exit 2 when every niche failed — signals genuine API problem.
    if stats["niches_processed"] == 0 and stats["niches_failed"] > 0:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
