"""Auto-sync commission rates from network APIs.

Checks if commission rates have changed and updates the catalog.
Logs changes for auditing.

Usage:
    python -m genlab_core.monetization.commission_sync
    python -m genlab_core.monetization.commission_sync --dry-run
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_CATALOG_PATH = Path(__file__).parent.parent.parent.parent / "config" / "affiliate_catalog.yaml"

# Known commission rates per network (updated manually or via API)
# These serve as the "source of truth" to check against catalog values
_KNOWN_RATES: dict[str, dict[str, float]] = {
    "amazon": {
        "hardware": 3.0,
        "peripheral": 4.0,
        "subscription": 2.0,
        "gear": 5.0,
        "merchandise": 5.0,
        "tool": 4.0,
    },
    "amazon_us": {
        "hardware": 3.0,
        "peripheral": 4.0,
        "subscription": 2.0,
        "gear": 4.0,
        "merchandise": 4.5,
        "tool": 4.0,
    },
    "cuelinks": {
        "_default": 7.0,
    },
}


def check_rates(catalog: dict) -> list[dict]:
    """Compare catalog commission rates against known rates.

    Returns list of dicts with: product, network, catalog_rate, expected_rate, status
    """
    changes = []
    for niche_id, niche_data in catalog.get("niches", {}).items():
        for product in niche_data.get("products", []):
            name = product.get("name", "?")
            category = product.get("category", "")
            for network, info in product.get("networks", {}).items():
                if "example.com" in info.get("url", ""):
                    continue

                catalog_rate = info.get("commission_pct", 0)
                known = _KNOWN_RATES.get(network, {})
                expected = known.get(category, known.get("_default"))

                if expected is not None and abs(catalog_rate - expected) > 0.01:
                    changes.append(
                        {
                            "product": name,
                            "niche": niche_id,
                            "network": network,
                            "category": category,
                            "catalog_rate": catalog_rate,
                            "expected_rate": expected,
                            "status": "rate_mismatch",
                        }
                    )

    return changes


def main():
    parser = argparse.ArgumentParser(description="Sync commission rates")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # 2026-07-17: delegate to `catalog_loader.load_catalog` so
    # ${AMAZON_US_AFFILIATE_TAG} placeholders in URLs get expanded via
    # os.path.expandvars. Prior state read raw YAML → commission
    # attribution was computing rates against unexpanded URLs, silently
    # mis-attributing revenue rows to the wrong affiliate. See
    # session-2026-07-17 audit round 2 agent 3 (H4).
    from genlab_core.monetization.catalog_loader import load_catalog

    catalog = load_catalog(Path(_CATALOG_PATH))

    changes = check_rates(catalog)

    if not changes:
        logger.info("All commission rates match expected values. No changes needed.")
        return

    logger.info("Found %d rate mismatches:\n", len(changes))
    for c in changes:
        logger.info(
            "  %s / %s / %s: catalog=%.1f%% expected=%.1f%%",
            c["niche"],
            c["product"],
            c["network"],
            c["catalog_rate"],
            c["expected_rate"],
        )

    if not args.dry_run:
        logger.info("\nTo update, edit the catalog YAML or re-run with --fix (not yet implemented)")


if __name__ == "__main__":
    main()
