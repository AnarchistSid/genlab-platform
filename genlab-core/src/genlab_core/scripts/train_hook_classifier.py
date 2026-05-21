#!/usr/bin/env python3
"""Train the hook quality classifier for all niches.

Pulls labelled training data from PendingFeedback + Blueprints,
trains an XGBoost classifier per niche, and saves to genlab-core/models/.

Usage:
    uv run --package genlab-core python -m genlab_core.scripts.train_hook_classifier
    uv run --package genlab-core python -m genlab_core.scripts.train_hook_classifier --niche-id gaming
"""

from __future__ import annotations

import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("genlab.train_hooks")

ALL_NICHES = ["ai_creators", "gaming", "sports", "movies", "anime"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Train hook quality classifier")
    parser.add_argument("--niche-id", default="all", choices=ALL_NICHES + ["all"])
    parser.add_argument("--dry-run", action="store_true", help="Show data stats without training")
    args = parser.parse_args()

    # Source .env for DATABASE_URL
    try:
        from pathlib import Path

        from dotenv import load_dotenv

        genlab_root = Path(__file__).resolve().parents[4]
        load_dotenv(genlab_root / ".env", override=False)
    except ImportError:
        pass

    niches = ALL_NICHES if args.niche_id == "all" else [args.niche_id]

    try:
        from genlab_core.http.backlog_client import BacklogClient
        from genlab_core.learning.hook_classifier import train_and_save
        from genlab_core.learning.hook_training_data import (
            MIN_EXAMPLES,
            compute_engagement_labels,
            pull_training_data,
        )
    except ImportError as e:
        logger.error("Required modules not available: %s", e)
        return 1

    client = BacklogClient()
    total_trained = 0
    for niche_id in niches:
        logger.info("=== %s ===", niche_id)
        try:
            examples = pull_training_data(client, niche_id=niche_id)
            logger.info("  Examples: %d (threshold: %d)", len(examples), MIN_EXAMPLES)

            if len(examples) < MIN_EXAMPLES:
                logger.info("  Skipping — not enough examples yet")
                continue

            labels = compute_engagement_labels(examples)

            if args.dry_run:
                pos = sum(labels)
                logger.info("  Dry run — %d positive / %d negative", pos, len(labels) - pos)
                continue

            # train_and_save signature is (examples, labels, niche_id=...)
            # — the previous positional call (niche_id, examples, labels)
            # passed the niche string as `examples`, so len(examples)
            # collapsed to the string length and the function bailed
            # at the MIN_EXAMPLES check while this script logged
            # "Trained and saved successfully" anyway. We never noticed
            # because there were 0 examples to train on (the ID-bridge
            # bug above this).
            ok = train_and_save(examples, labels, niche_id=niche_id)
            if ok:
                total_trained += 1
                logger.info("  Trained and saved successfully")
            else:
                logger.info("  Training skipped or failed")
        except Exception:
            logger.exception("  Training failed for %s", niche_id)

    logger.info("Done: %d/%d niches trained", total_trained, len(niches))
    return 0


if __name__ == "__main__":
    sys.exit(main())
