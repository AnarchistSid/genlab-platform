"""Tests for the PA-API product embeddings bootstrap script (fix #7).

Pins the curated query set + the bootstrap dispatch loop. The actual
PA-API call + embedding write is mocked; this test verifies the seed
catalog stays sane and the loop respects the dry-run + per-niche flags.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from genlab_core.scripts.bootstrap_product_embeddings import (
    _BOOTSTRAP_QUERIES,
    bootstrap_niche,
)


class TestSeedCatalog:
    def test_all_five_niches_have_queries(self):
        for niche in ("gaming", "sports", "movies", "anime", "ai_creators"):
            assert niche in _BOOTSTRAP_QUERIES
            assert len(_BOOTSTRAP_QUERIES[niche]) >= 5, (
                f"Niche {niche} needs ≥5 seed queries to populate the "
                "embedding index meaningfully — fewer = sparse semantic "
                "matching = no affiliate recommendation."
            )

    def test_queries_use_valid_search_index(self):
        """Pin against accidental typos in SearchIndex names; bad values
        get 400 from PA-API silently and the bootstrap completes with
        0 products."""
        # The full list is documented at
        # https://webservices.amazon.com/paapi5/documentation/locale-reference/north-america.html
        # Pin the most common ones we actually use here so a typo trips
        # a deterministic test rather than an opaque PA-API error.
        valid_indexes = {
            "All",
            "Apparel",
            "Books",
            "Electronics",
            "GroceryAndGourmetFood",
            "HomeAndKitchen",
            "MoviesAndTV",
            "OfficeProducts",
            "Shoes",
            "SportsAndOutdoors",
            "ToysAndGames",
            "VideoGames",
        }
        for niche_id, queries in _BOOTSTRAP_QUERIES.items():
            for _keywords, search_index in queries:
                assert search_index in valid_indexes, (
                    f"Invalid SearchIndex {search_index!r} for niche "
                    f"{niche_id} — PA-API will silently 400."
                )


class TestBootstrapNiche:
    def _make_product(self, asin: str, title: str = "X", price: int = 999):
        from genlab_core.monetization.paapi_client import PaapiProduct

        return PaapiProduct(
            asin=asin,
            title=title,
            price=price,
            currency="USD",
            image_url="",
            detail_url="",
        )

    def test_dry_run_does_not_call_index(self):
        matcher = MagicMock()
        paapi = MagicMock()
        paapi.search.return_value = [self._make_product("ASIN1")]

        stats = bootstrap_niche("gaming", matcher, paapi, dry_run=True)

        matcher.index_product.assert_not_called()
        # dry-run still counts "would-index" products in the stats
        assert stats["indexed"] >= 1
        assert stats["skipped"] == 0

    def test_writes_via_matcher_on_real_run(self):
        matcher = MagicMock()
        matcher.index_product.return_value = True
        paapi = MagicMock()
        paapi.search.return_value = [
            self._make_product("ASIN1"),
            self._make_product("ASIN2"),
        ]

        stats = bootstrap_niche("gaming", matcher, paapi, dry_run=False)

        # 10 queries × 2 products = 20 products attempted (we set side
        # effect to a fixed list, so the same list returns each query).
        assert matcher.index_product.call_count == 20
        assert stats["indexed"] == 20

    def test_failed_index_counted_as_skipped(self):
        matcher = MagicMock()
        matcher.index_product.return_value = False  # all fail
        paapi = MagicMock()
        paapi.search.return_value = [self._make_product("ASIN1")]

        stats = bootstrap_niche("gaming", matcher, paapi, dry_run=False)

        assert stats["indexed"] == 0
        assert stats["skipped"] == matcher.index_product.call_count

    def test_search_exception_does_not_kill_loop(self):
        """One failed search call shouldn't abort the entire bootstrap."""
        matcher = MagicMock()
        paapi = MagicMock()
        paapi.search.side_effect = [
            RuntimeError("PA-API rate limit"),
            [self._make_product("ASIN1")],
            *([RuntimeError("rate limit")] * 8),  # rest fail
        ]

        stats = bootstrap_niche("gaming", matcher, paapi, dry_run=False)

        # The 1 successful search produced 1 indexed product; the rest were silent
        assert stats["queried"] == 10
        assert stats["indexed"] == 1

    def test_unknown_niche_returns_zero_stats(self):
        matcher = MagicMock()
        paapi = MagicMock()

        stats = bootstrap_niche("nonexistent", matcher, paapi, dry_run=False)

        assert stats == {"queried": 0, "indexed": 0, "skipped": 0}
        paapi.search.assert_not_called()
