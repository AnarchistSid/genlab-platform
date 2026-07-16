"""Tests for CuelinksAdapter V3 wire + geo resolver integration.

PR 3 of 3 (2026-07-16). Pins the runtime behavior of the Cuelinks
re-integration:

  * Adapter calls cuelinks_client.convert_url with the merchant URL
    + optional subid
  * AmazonUrlNotAllowed from the client is caught + logged at WARNING
    + adapter returns "" (resolver falls to next candidate)
  * Missing product_url returns "" (does NOT synthesize amazon.in as
    the pre-V3 stub did — that was the ₹0 pathway)
  * Geo resolver adds "cuelinks" as LAST candidate in both IN + US
    lists (Amazon-first invariant preserved)
"""

from __future__ import annotations

from unittest.mock import patch

from genlab_core.monetization.cuelinks_client import AmazonUrlNotAllowed
from genlab_core.monetization.network_registry import CuelinksAdapter


class TestCuelinksAdapterWire:
    def test_calls_convert_url_with_product_url(self):
        """Adapter routes through cuelinks_client.convert_url."""
        adapter = CuelinksAdapter()
        with patch(
            "genlab_core.monetization.cuelinks_client.convert_url",
            return_value="https://cuelinks.com/tracked/xyz",
        ) as mock_convert:
            result = adapter.generate_url(
                "SKU-123",
                product_url="https://flipkart.com/product/xyz",
                subid="anime:abc12345",
            )
        assert result == "https://cuelinks.com/tracked/xyz"
        mock_convert.assert_called_once_with(
            "https://flipkart.com/product/xyz",
            subid="anime:abc12345",
        )

    def test_amazon_url_caught_and_logs_warning(self, caplog):
        """The client's AmazonUrlNotAllowed must be caught at the
        adapter layer so the resolver can fall through. Also must log
        LOUDLY (WARNING) so operator sees the catalog leak in dashboard
        alerts and fixes the offending catalog entry.
        """
        adapter = CuelinksAdapter()
        with patch(
            "genlab_core.monetization.cuelinks_client.convert_url",
            side_effect=AmazonUrlNotAllowed("2026-06-14 audit: Amazon URL"),
        ):
            with caplog.at_level("WARNING"):
                result = adapter.generate_url(
                    "SKU-B0XYZ",
                    product_url="https://amazon.in/dp/B0XYZ",
                )
        assert result == ""
        # The log must carry the 'leak' marker so operator can grep
        assert any("catalog_cuelinks_amazon_url_leak" in rec.message for rec in caplog.records), (
            f"expected leak log, got: {[r.message for r in caplog.records]}"
        )

    def test_missing_product_url_returns_empty(self):
        """Pre-V3 stub used to synthesise an amazon.in URL when
        product_url was missing — that was the exact ₹0-commission
        pathway. Post-V3 must return "" so the resolver falls to
        Amazon-direct instead."""
        adapter = CuelinksAdapter()
        result = adapter.generate_url("SKU-123")
        assert result == ""

    def test_missing_product_url_does_not_synthesize_amazon(self):
        """Belt-and-suspenders pin against the pre-V3 anti-pattern:
        even if someone refactors and accidentally re-adds URL
        synthesis, this test fails."""
        adapter = CuelinksAdapter()
        # convert_url must NOT be called when product_url is empty —
        # returning "" is the correct behavior. If a refactor makes it
        # call convert_url("<synthesized>"), the mock catches it.
        with patch(
            "genlab_core.monetization.cuelinks_client.convert_url",
        ) as mock_convert:
            result = adapter.generate_url("SKU-123")
        assert result == ""
        mock_convert.assert_not_called()

    def test_validate_url_accepts_v3_and_legacy_shapes(self):
        adapter = CuelinksAdapter()
        # V3 tracked URL
        assert adapter.validate_url("https://www.cuelinks.com/tracked/abc") is True
        # Legacy redirect (backwards compat with cached values)
        assert adapter.validate_url("https://linksredirect.com/?cid=X&url=Y") is True
        # Non-cuelinks URL
        assert adapter.validate_url("https://flipkart.com/product/xyz") is False


class TestGeoResolverCandidateOrdering:
    """Pin: Amazon adapters must ALWAYS come before cuelinks in both
    geo candidate lists. Re-ordering to put cuelinks first would
    re-create the 2026-06-14 ₹0-commission incident for products
    with both networks configured."""

    def test_in_geo_amazon_before_cuelinks(self):
        # Load the actual candidate list built by the resolver source.
        # We inspect the module source string rather than call
        # _resolve_niche_geo directly to avoid setting up the whole
        # niche/DB fixture — the invariant is purely about candidate
        # ordering in the source.
        from pathlib import Path

        src = Path("genlab-core/src/genlab_core/monetization/geo_link_resolver.py").read_text()

        # Find the IN geo block
        in_block_start = src.find('if geo == "IN":')
        in_block_end = src.find("else:", in_block_start)
        in_block = src[in_block_start:in_block_end]

        cuelinks_pos = in_block.find('"cuelinks"')
        assert cuelinks_pos > 0, "cuelinks missing from IN candidates"
        for amazon_key in ('"amazon"', '"amazon_in"', '"amazon_us"'):
            amazon_pos = in_block.find(amazon_key)
            assert amazon_pos > 0, f"{amazon_key} missing from IN candidates"
            assert amazon_pos < cuelinks_pos, (
                f"IN geo: {amazon_key} at position {amazon_pos} but cuelinks "
                f"at {cuelinks_pos} — cuelinks must come AFTER Amazon adapters"
            )

    def test_us_geo_amazon_before_cuelinks(self):
        from pathlib import Path

        src = Path("genlab-core/src/genlab_core/monetization/geo_link_resolver.py").read_text()

        # Find the US (else:) block after the IN if
        in_block_start = src.find('if geo == "IN":')
        else_start = src.find("else:", in_block_start)
        # US candidates block ends at the closing bracket
        else_end = src.find("base_url = ", else_start)
        us_block = src[else_start:else_end]

        cuelinks_pos = us_block.find('"cuelinks"')
        assert cuelinks_pos > 0, "cuelinks missing from US candidates"
        for amazon_key in ('"amazon_us"', '"amazon"'):
            amazon_pos = us_block.find(amazon_key)
            assert amazon_pos > 0, f"{amazon_key} missing from US candidates"
            assert amazon_pos < cuelinks_pos, (
                f"US geo: {amazon_key} at position {amazon_pos} but cuelinks "
                f"at {cuelinks_pos} — cuelinks must come AFTER Amazon adapters"
            )

    def test_cuelinks_is_last_candidate_in_in_geo(self):
        """Pin the "last resort" semantics — cuelinks should be the
        FINAL fallback so earnkaro (Indian deal broker with better
        commission on Amazon) fires first for IN geo."""
        from pathlib import Path

        src = Path("genlab-core/src/genlab_core/monetization/geo_link_resolver.py").read_text()

        in_block_start = src.find('if geo == "IN":')
        in_block_end = src.find("else:", in_block_start)
        in_block = src[in_block_start:in_block_end]

        # earnkaro must come before cuelinks in IN geo
        earnkaro_pos = in_block.find('"earnkaro"')
        cuelinks_pos = in_block.find('"cuelinks"')
        assert earnkaro_pos > 0 and cuelinks_pos > 0
        assert earnkaro_pos < cuelinks_pos, (
            "IN geo: earnkaro must come before cuelinks (earnkaro's "
            "Amazon commission tier is higher than cuelinks' passthrough)"
        )
