#!/usr/bin/env python3
"""Apply warm-start priors from source niches to target niches.

Transfers learned Thompson Sampling arm posteriors using confidence-scaled
Beta priors. Only touches arms at Beta(1,1) — never overwrites real data.

Usage:
    python scripts/apply_warm_start.py --target sports
    python scripts/apply_warm_start.py --all
    python scripts/apply_warm_start.py --target sports --dry-run
"""
from __future__ import annotations

import argparse
import logging
import sys

from genlab_core.learning.arm_loader import BANDIT_LIST_NAMES, load_all_arms
from genlab_core.learning.meta_prior import TRANSFER_MATRIX, apply_warm_start

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def _build_proxy(list_name: str):
    """Build a GraphTableProxy for a bandit arms list by display name."""
    from genlab_core.http.async_bridge import run_async
    from genlab_core.settings import settings

    from azure.identity import ClientSecretCredential
    from msgraph import GraphServiceClient

    credential = ClientSecretCredential(
        tenant_id=settings.azure_tenant_id,
        client_id=settings.azure_client_id,
        client_secret=settings.azure_client_secret,
    )
    graph = GraphServiceClient(
        credential, scopes=["https://graph.microsoft.com/.default"]
    )
    site_id = settings.sharepoint_site_id

    # Resolve list_id from display name
    async def _resolve():
        lists_result = await graph.sites.by_site_id(site_id).lists.get()
        for lst in (lists_result.value or []):
            if lst.display_name == list_name:
                return lst.id
        raise ValueError(f"List '{list_name}' not found in SharePoint site")

    list_id = run_async(_resolve())

    from genlab_core.http.graph_proxy import GraphTableProxy

    return GraphTableProxy(graph, site_id, list_id, list_name)


def _run_transfer(target_niche: str, dry_run: bool) -> dict[str, str]:
    """Transfer priors from source to target niche."""
    transfers = TRANSFER_MATRIX.get(target_niche)
    if not transfers:
        logger.error("No transfer mapping for '%s'", target_niche)
        return {}

    source_niche = next(iter(transfers))
    source_list = BANDIT_LIST_NAMES.get(source_niche)
    target_list = BANDIT_LIST_NAMES.get(target_niche)

    if not source_list:
        logger.error("No bandit list configured for source '%s'", source_niche)
        return {}
    if not target_list:
        logger.error("No bandit list configured for target '%s'", target_niche)
        return {}

    logger.info(
        "Loading source arms from %s (%s)...", source_niche, source_list
    )
    source_proxy = _build_proxy(source_list)
    source_arms = load_all_arms(source_proxy, source_niche)

    if not source_arms:
        logger.warning("No arms found in source niche '%s'", source_niche)
        return {}

    logger.info(
        "Loaded %d arms from %s. Transferring to %s (%s)...",
        len(source_arms),
        source_niche,
        target_niche,
        target_list,
    )

    target_proxy = _build_proxy(target_list)
    results = apply_warm_start(
        target_niche, source_arms, target_proxy, dry_run=dry_run
    )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply warm-start priors from source to target niches"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--target",
        choices=list(TRANSFER_MATRIX.keys()),
        help="Target niche to warm-start",
    )
    group.add_argument(
        "--all",
        action="store_true",
        dest="all_niches",
        help="Warm-start all target niches",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute priors without writing to SharePoint",
    )
    args = parser.parse_args()

    targets = list(TRANSFER_MATRIX.keys()) if args.all_niches else [args.target]
    total_updated = 0
    total_skipped = 0

    for target in targets:
        logger.info("=== Processing %s ===", target)
        results = _run_transfer(target, args.dry_run)
        updated = sum(1 for v in results.values() if v == "updated")
        skipped = sum(1 for v in results.values() if v == "skipped_has_obs")
        dry = sum(1 for v in results.values() if v == "dry_run")
        total_updated += updated
        total_skipped += skipped
        logger.info(
            "%s: %d updated, %d skipped, %d dry-run",
            target,
            updated,
            skipped,
            dry,
        )

    logger.info(
        "=== Done. Total: %d updated, %d skipped ===",
        total_updated,
        total_skipped,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
