"""Bootstrap the product_embeddings index from PA-API search results.

Fix #7 of the autonomy roadmap. The product_embeddings Postgres table is
the semantic index that AffiliateMatch uses to pick the best product
recommendation per blueprint. The table exists with the correct schema
but has zero rows on production as of 2026-06-13 because the bootstrap
was never run after PA-API SigV4 (PR #168) shipped.

This script searches Amazon PA-API for a curated set of niche-keyword
combinations, embeds each result's text, and upserts it into
`product_embeddings`. Idempotent — re-running just refreshes existing
rows via the ON CONFLICT DO UPDATE clause in
:meth:`EmbeddingMatcher.index_product`.

Usage:
    python -m genlab_core.scripts.bootstrap_product_embeddings
    python -m genlab_core.scripts.bootstrap_product_embeddings --niche gaming
    python -m genlab_core.scripts.bootstrap_product_embeddings --dry-run

After this script populates the table, the bandit-aware affiliate matcher
can finally serve product recommendations on sports/ai_creators blueprints
(currently 16.8% / 24.8% affiliate-attachment respectively — the
under-attached niches per § B.7 of the gap analysis).
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import Final

logger = logging.getLogger(__name__)


# Curated seed keywords per niche. The PA-API SearchIndex narrows the
# Amazon catalog by department; the keywords sit on top.
#
# Goal: 30-60 products per niche after bootstrap. The embedding-matcher
# then picks the top_k=3 most semantically similar to each blueprint at
# render time, so coverage matters more than precision.
_BOOTSTRAP_QUERIES: Final[dict[str, list[tuple[str, str]]]] = {
    "gaming": [
        ("gaming headset wireless", "Electronics"),
        ("mechanical keyboard rgb", "Electronics"),
        ("gaming mouse wireless", "Electronics"),
        ("gaming chair ergonomic", "HomeAndKitchen"),
        ("nintendo switch accessories", "VideoGames"),
        ("playstation 5 accessories", "VideoGames"),
        ("xbox series x accessories", "VideoGames"),
        ("streaming microphone usb", "Electronics"),
        ("gaming monitor 144hz", "Electronics"),
        ("ring light streaming", "Electronics"),
    ],
    "sports": [
        ("running shoes men", "Shoes"),
        ("running shoes women", "Shoes"),
        ("basketball jersey", "Apparel"),
        ("nfl jersey", "Apparel"),
        ("home gym equipment", "SportsAndOutdoors"),
        ("yoga mat thick", "SportsAndOutdoors"),
        ("smart watch fitness", "Electronics"),
        ("compression sleeve", "SportsAndOutdoors"),
        ("soccer cleats", "Shoes"),
        ("mma gloves", "SportsAndOutdoors"),
    ],
    "movies": [
        ("4k ultra hd blu ray", "MoviesAndTV"),
        ("home theater soundbar", "Electronics"),
        ("popcorn maker electric", "HomeAndKitchen"),
        ("4k projector home", "Electronics"),
        ("smart tv 65 inch", "Electronics"),
        ("movie collector poster", "HomeAndKitchen"),
        ("streaming device 4k", "Electronics"),
        ("comfortable recliner movie", "HomeAndKitchen"),
    ],
    "anime": [
        ("anime figure collectible", "ToysAndGames"),
        ("manga book set", "Books"),
        ("anime poster", "HomeAndKitchen"),
        ("cosplay accessories", "Apparel"),
        ("anime tshirt", "Apparel"),
        ("anime backpack", "Apparel"),
        ("japanese snack box", "GroceryAndGourmetFood"),
        ("anime keychain set", "ToysAndGames"),
        ("graphic novel manga", "Books"),
    ],
    "ai_creators": [
        ("usb microphone podcast", "Electronics"),
        ("webcam 4k streaming", "Electronics"),
        ("ai book artificial intelligence", "Books"),
        ("portable ssd 1tb", "Electronics"),
        ("notion productivity book", "Books"),
        ("standing desk converter", "OfficeProducts"),
        ("noise cancelling headphones", "Electronics"),
        ("usb-c hub multiport", "Electronics"),
        ("creator monitor 27 inch", "Electronics"),
    ],
}


def bootstrap_niche(
    niche_id: str,
    matcher,  # EmbeddingMatcher
    paapi,  # PaapiClient
    dry_run: bool = False,
    min_price: int = 500,
    max_results_per_query: int = 5,
) -> dict[str, int]:
    """Run the bootstrap queries for one niche.

    Returns a stats dict: ``{"queried": N, "indexed": N, "skipped": N}``.
    """
    queries = _BOOTSTRAP_QUERIES.get(niche_id, [])
    if not queries:
        logger.warning("[bootstrap] No seed queries for niche=%s", niche_id)
        return {"queried": 0, "indexed": 0, "skipped": 0}

    stats = {"queried": 0, "indexed": 0, "skipped": 0}
    for keywords, search_index in queries:
        stats["queried"] += 1
        logger.info("[bootstrap] %s | searching %r in %s", niche_id, keywords, search_index)
        try:
            products = paapi.search(
                keywords=keywords,
                search_index=search_index,
                min_price=min_price,
                max_results=max_results_per_query,
            )
        except Exception as exc:
            logger.warning("[bootstrap] PA-API search failed: %s", exc)
            continue

        if not products:
            logger.info("[bootstrap] No products returned for %r", keywords)
            continue

        for product in products:
            if dry_run:
                logger.info(
                    "[bootstrap:DRY] would index %s | %s | $%s",
                    product.asin,
                    product.title[:60],
                    product.price,
                )
                stats["indexed"] += 1
                continue

            ok = matcher.index_product(
                product_id=product.asin,
                name=product.title,
                category=product.category or search_index,
                niche_ids=[niche_id],
                description="",
            )
            if ok:
                stats["indexed"] += 1
            else:
                stats["skipped"] += 1

        # Be polite to PA-API — strict throttle of 1 TPS per Amazon docs.
        time.sleep(1.1)

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--niche",
        choices=sorted({*_BOOTSTRAP_QUERIES.keys(), "all"}),
        default="all",
        help="Niche to bootstrap, or 'all' (default).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be indexed; don't write to DB.",
    )
    parser.add_argument(
        "--min-price",
        type=int,
        default=500,
        help="Minimum product price in cents (default: 500 = $5.00).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Lazy imports so the module is importable even when PA-API or
    # the embedding stack isn't installed (matches affiliate_matcher
    # tolerance — the bootstrap is OPT-IN, never run during normal
    # pipelines).
    from genlab_core.monetization.embedding_matcher import EmbeddingMatcher
    from genlab_core.monetization.paapi_client import PaapiClient

    matcher = EmbeddingMatcher()
    paapi = PaapiClient()

    if not paapi.is_configured:
        logger.error(
            "[bootstrap] PA-API not configured — set PAAPI_ACCESS_KEY + "
            "PAAPI_SECRET_KEY + AMAZON_US_AFFILIATE_TAG in .env"
        )
        return 2

    # `available` is an EmbeddingMatcher @property — call without parens.
    # The pre-fix `matcher.available()` raised TypeError ("'bool' object is
    # not callable"), which was masked by tests that MagicMock the matcher
    # (mock.available() returns a Mock, not a bool — never errors).
    # Also: the EmbeddingMatcher uses OpenAI text-embedding-3-small, NOT
    # sentence-transformers; the old error message pointed at the wrong dep.
    if not args.dry_run and not matcher.available:
        logger.error(
            "[bootstrap] EmbeddingMatcher not available — set OPENAI_API_KEY "
            "and verify DATABASE_URL points at a Postgres with the "
            "product_embeddings table (pgvector extension required)"
        )
        return 3

    niches = sorted(_BOOTSTRAP_QUERIES.keys()) if args.niche == "all" else [args.niche]
    grand_total = {"queried": 0, "indexed": 0, "skipped": 0}

    for niche_id in niches:
        logger.info("[bootstrap] === starting %s ===", niche_id)
        stats = bootstrap_niche(
            niche_id=niche_id,
            matcher=matcher,
            paapi=paapi,
            dry_run=args.dry_run,
            min_price=args.min_price,
        )
        logger.info(
            "[bootstrap] %s done | queried=%d indexed=%d skipped=%d",
            niche_id,
            stats["queried"],
            stats["indexed"],
            stats["skipped"],
        )
        for k in grand_total:
            grand_total[k] += stats[k]

    logger.info(
        "[bootstrap] DONE | %d queries, %d products indexed, %d skipped",
        grand_total["queried"],
        grand_total["indexed"],
        grand_total["skipped"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
