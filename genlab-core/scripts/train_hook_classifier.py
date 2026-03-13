#!/usr/bin/env python3
"""Train the hook quality classifier from SharePoint engagement data.

Usage:
    # Train for a single niche
    uv run --package genlab-core python genlab-core/scripts/train_hook_classifier.py --niche ai_news

    # Train for all niches
    uv run --package genlab-core python genlab-core/scripts/train_hook_classifier.py --all

Requires:
    - Azure/SharePoint credentials configured (.env)
    - xgboost installed (optional dep — training skips gracefully if missing)
    - At least 200 completed feedback examples per niche
"""
from __future__ import annotations

import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("train_hook_classifier")

# All known niches
ALL_NICHES = ["ai_news", "gaming", "sports", "movies", "anime"]


def train_niche(niche_id: str) -> bool:
    """Train the hook classifier for one niche."""
    from genlab_core.learning.hook_classifier import train_and_save
    from genlab_core.learning.hook_training_data import (
        compute_engagement_labels,
        pull_training_data,
    )

    logger.info("=" * 60)
    logger.info("Training hook classifier for niche: %s", niche_id)
    logger.info("=" * 60)

    # Initialize BacklogClient
    try:
        from genlab_core.http.backlog_client import BacklogClient

        client = BacklogClient()
    except Exception as exc:
        logger.error("Failed to initialize BacklogClient: %s", exc)
        return False

    # Pull training data
    examples = pull_training_data(client, niche_id=niche_id)
    if not examples:
        logger.info("No training examples found for %s — nothing to train.", niche_id)
        return False

    logger.info("Pulled %d examples for %s", len(examples), niche_id)

    # Compute labels
    labels = compute_engagement_labels(examples)

    # Train and save
    success = train_and_save(examples, labels, niche_id=niche_id)
    if success:
        logger.info("Training complete for %s", niche_id)
    else:
        logger.info(
            "Training skipped for %s (likely < 200 examples or xgboost missing)",
            niche_id,
        )

    return success


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train hook quality classifier from engagement data",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--niche",
        help="Train for a single niche (e.g. ai_news, gaming)",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Train for all known niches",
    )
    args = parser.parse_args()

    niches = ALL_NICHES if args.all else [args.niche]
    results: dict[str, bool] = {}

    for niche_id in niches:
        results[niche_id] = train_niche(niche_id)

    # Summary
    print("\n" + "=" * 60)
    print("Training Summary")
    print("=" * 60)
    for niche_id, success in results.items():
        status = "TRAINED" if success else "SKIPPED"
        print(f"  {niche_id}: {status}")

    trained = sum(1 for v in results.values() if v)
    print(f"\n{trained}/{len(results)} niches trained successfully.")

    sys.exit(0 if trained > 0 or not any(results.values()) else 1)


if __name__ == "__main__":
    main()
