"""Affiliate product matching pipeline stage.

Scans story content for keyword matches against the affiliate catalog and
selects the best network (highest commission) for each match.

Usage:
    stage = AffiliateMatch()
    context = stage.execute(context)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Absolute path to the shared affiliate catalog.
# Path: affiliate_matcher.py → monetization/ → genlab_core/ → src/ → genlab-core/
_CATALOG_PATH = Path(__file__).parent.parent.parent.parent / "config" / "affiliate_catalog.yaml"


def _load_catalog(catalog_path: Path | None = None) -> dict[str, Any]:
    """Load the affiliate catalog YAML from disk."""
    path = catalog_path or _CATALOG_PATH
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def match_product(
    text: str,
    niche_id: str,
    catalog: dict[str, Any],
) -> dict[str, Any] | None:
    """Scan text for keyword matches against niche products.

    Returns the product dict with the most keyword hits, or None if no match.
    """
    niche_products = (catalog.get("niches") or {}).get(niche_id, {}).get("products", [])
    if not niche_products:
        return None

    text_lower = text.lower()
    best_product: dict[str, Any] | None = None
    best_hits = 0

    for product in niche_products:
        keywords = product.get("keywords") or []
        hits = sum(1 for kw in keywords if kw.lower() in text_lower)
        if hits > best_hits:
            best_hits = hits
            best_product = product

    return best_product if best_hits > 0 else None


def select_best_network(product: dict[str, Any]) -> tuple[str, str, float]:
    """Return (network_name, url, commission_pct) for the network with the highest commission.

    Falls back to the first available network if multiple are tied.
    """
    networks: dict[str, dict[str, Any]] = product.get("networks") or {}
    if not networks:
        return ("", "", 0.0)

    best_name = ""
    best_url = ""
    best_commission = -1.0

    for name, info in networks.items():
        commission = float(info.get("commission_pct", 0.0))
        if commission > best_commission:
            best_commission = commission
            best_name = name
            best_url = info.get("url", "")

    return (best_name, best_url, best_commission)


class AffiliateMatch:
    """Pipeline stage: match affiliate products to stories.

    Loads the affiliate catalog, scans each story's text for keyword matches,
    selects the best affiliate network, and enriches the story dict with
    affiliate fields. Non-fatal: stories without a match are left unchanged.
    """

    def __init__(self, catalog_path: Path | None = None) -> None:
        self._catalog_path = catalog_path

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        stories: list[dict[str, Any]] = context.get("stories", [])
        if not stories:
            logger.info("[AffiliateMatch] No stories to process")
            context.setdefault("run_stats", {})["affiliate"] = {
                "matched": 0, "skipped": 0, "cap_enforced": 0,
            }
            return context

        niche_id: str = context.get("niche_id", "")

        # Load catalog
        try:
            catalog = _load_catalog(self._catalog_path)
        except Exception as exc:
            logger.warning("[AffiliateMatch] Could not load catalog: %s — skipping", exc)
            context.setdefault("run_stats", {})["affiliate"] = {
                "matched": 0, "skipped": len(stories), "cap_enforced": 0,
                "error": str(exc),
            }
            return context

        catalog_settings: dict[str, Any] = catalog.get("settings") or {}
        max_per_day: int = int(catalog_settings.get("max_affiliate_posts_per_day", 3))
        disclosure_map: dict[str, str] = catalog_settings.get("disclosure_text") or {}

        matched = 0
        skipped = 0
        cap_enforced = 0

        for story in stories:
            # Respect daily cap
            if matched >= max_per_day:
                cap_enforced += 1
                logger.debug(
                    "[AffiliateMatch] Cap of %d reached — skipping story '%s'",
                    max_per_day,
                    story.get("title", "")[:60],
                )
                continue

            # Build search text from hook + caption + title
            content = story.get("content") or {}
            if isinstance(content, str):
                import json
                try:
                    content = json.loads(content)
                except (json.JSONDecodeError, TypeError):
                    content = {}

            hook = content.get("hook", "")
            ig_caption = (content.get("instagram") or {}).get("caption", "")
            title = story.get("title", "")
            search_text = f"{hook} {ig_caption} {title}"

            product = match_product(search_text, niche_id, catalog)
            if product is None:
                skipped += 1
                logger.debug(
                    "[AffiliateMatch] No product match for story '%s'",
                    title[:60],
                )
                continue

            network_name, url, commission_pct = select_best_network(product)
            if not url:
                skipped += 1
                logger.debug(
                    "[AffiliateMatch] Product '%s' has no network URL — skipping",
                    product.get("name", ""),
                )
                continue

            product_name: str = product.get("name", "")

            # Build CTA (platform-agnostic — detailed injection done in cta_engine)
            cta = f"🔗 {product_name} — link in bio"

            story["affiliate_product"] = product_name
            story["affiliate_url"] = url
            story["affiliate_network"] = network_name
            story["affiliate_commission_pct"] = commission_pct
            story["affiliate_cta"] = cta
            story["_affiliate_disclosure_map"] = disclosure_map

            matched += 1
            logger.info(
                "[AffiliateMatch] Matched '%s' → %s via %s (%.1f%%)",
                title[:60],
                product_name,
                network_name,
                commission_pct,
            )

        context.setdefault("run_stats", {})["affiliate"] = {
            "matched": matched,
            "skipped": skipped,
            "cap_enforced": cap_enforced,
        }
        logger.info(
            "[AffiliateMatch] %d matched, %d skipped, %d cap-enforced",
            matched, skipped, cap_enforced,
        )
        return context
