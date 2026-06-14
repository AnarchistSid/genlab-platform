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


class TestMainAvailabilityCheck:
    """Regression pins for the 2026-06-14 ``available()`` bug.

    EmbeddingMatcher exposes ``available`` as a ``@property``; the
    bootstrap script's ``main()`` previously called it as
    ``matcher.available()`` which would raise ``TypeError: 'bool' object
    is not callable`` on any non-dry-run invocation. The bug escaped
    because the only pre-existing tests mocked the matcher with
    ``MagicMock()`` — and a MagicMock's ``.available()`` returns another
    Mock (truthy) rather than tripping the type error.

    These pins call ``main()`` directly with a stubbed
    ``EmbeddingMatcher`` whose ``available`` is a real bool, so a
    regression to ``matcher.available()`` would crash deterministically.
    """

    def _stubs(self, available: bool):
        """Build matcher/paapi class stubs matching the lazy-imported
        types. ``available`` is a real bool attribute (NOT a method) so
        a regression to ``matcher.available()`` would TypeError."""

        class StubPaapi:
            is_configured = True

            def search(self, **_kw):
                return []  # no products → no index calls → fast test

        class StubMatcher:
            def __init__(self):
                self.available = available  # property-equivalent — real bool

            def index_product(self, **_kw):
                return True

        return StubMatcher, StubPaapi

    def test_main_with_unavailable_matcher_exits_3_without_type_error(self, monkeypatch):
        """The unavailable path must return exit 3 cleanly. Pre-fix, this
        raised TypeError on the `matcher.available()` call before getting
        a chance to return 3."""
        from genlab_core.scripts import bootstrap_product_embeddings as boot

        StubMatcher, StubPaapi = self._stubs(available=False)
        # ``main()`` does ``from genlab_core.monetization.{embedding_matcher,
        # paapi_client} import {EmbeddingMatcher, PaapiClient}`` lazily.
        # Patch the source modules' classes so the lazy import resolves
        # to our stub.
        monkeypatch.setattr(
            "genlab_core.monetization.embedding_matcher.EmbeddingMatcher",
            StubMatcher,
        )
        monkeypatch.setattr(
            "genlab_core.monetization.paapi_client.PaapiClient",
            StubPaapi,
        )
        # Argparse pulls from sys.argv; isolate from the test runner.
        monkeypatch.setattr("sys.argv", ["bootstrap_product_embeddings"])

        rc = boot.main()

        assert rc == 3, (
            "unavailable matcher must return exit 3; if this asserts "
            "with a TypeError trace, the `matcher.available()` regression "
            "is back"
        )

    def test_main_with_available_matcher_proceeds_past_the_gate(self, monkeypatch):
        """The available path must NOT short-circuit at the availability
        check. Pre-fix, even an available matcher tripped TypeError
        because the parens were the problem, not the bool value."""
        from genlab_core.scripts import bootstrap_product_embeddings as boot

        StubMatcher, StubPaapi = self._stubs(available=True)
        monkeypatch.setattr(
            "genlab_core.monetization.embedding_matcher.EmbeddingMatcher",
            StubMatcher,
        )
        monkeypatch.setattr(
            "genlab_core.monetization.paapi_client.PaapiClient",
            StubPaapi,
        )
        monkeypatch.setattr(
            "sys.argv",
            ["bootstrap_product_embeddings", "--niche", "gaming"],
        )
        # The script sleeps 1.1s between PA-API calls to be polite;
        # skip the sleep in tests.
        monkeypatch.setattr("time.sleep", lambda _s: None)

        rc = boot.main()

        # Exit 0: search returned no products so nothing was indexed,
        # but the run completed cleanly without TypeError.
        assert rc == 0
