"""CLI entry point for MonetisationTracker.

Usage:
    python -m genlab_core.monitoring.run_monetisation_tracker [--dry-run] [--niche NICHE] [--verbose]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Ensure Content Scraper .env is loaded for credentials
_GENLAB_ROOT = Path(__file__).resolve().parents[4]
_CS_ROOT = _GENLAB_ROOT / "Content Scraper"

try:
    from dotenv import load_dotenv
    load_dotenv(_CS_ROOT / ".env")
except ImportError:
    pass

os.environ.setdefault(
    "BACKLOG_CONFIG_PATH",
    str(_CS_ROOT / "config" / "lists_config.yaml"),
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect monetisation progress metrics from YT + FB + IG.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing to SP")
    parser.add_argument("--niche", default=None, help="Run for a single niche only")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger = logging.getLogger(__name__)

    from genlab_core.http.backlog_client import BacklogClient
    from genlab_core.monitoring.monetisation_tracker import MonetisationTracker

    client = BacklogClient()
    config_path = _GENLAB_ROOT / "genlab-core" / "config" / "monetisation_targets.yaml"
    tracker = MonetisationTracker(client, config_path=config_path)

    logger.info("Starting monetisation tracker (dry_run=%s, niche=%s)", args.dry_run, args.niche)
    results = tracker.run(dry_run=args.dry_run, niche_filter=args.niche)

    # Summary
    for niche_id, platforms in results.items():
        for platform, data in platforms.items():
            status = data.get("status", "unknown")
            metrics = data.get("metrics", {})
            logger.info(
                "[%s/%s] status=%s metrics=%s",
                niche_id, platform, status, metrics,
            )

    logger.info("Monetisation tracker complete.")


if __name__ == "__main__":
    main()
