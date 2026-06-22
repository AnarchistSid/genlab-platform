"""Pin: per-niche affiliate disable flag (2026-06-22 audit fix).

The 2026-06-22 affiliate audit found 4 of 5 niches had 12-19× worse
affiliate click-through than movies because of viewer-intent mismatch
(Ronaldo reel → Fitness Tracker, anime fight → random figure, etc.).

Until each niche has a per-reel-specific catalog, operators can
disable affiliate matching entirely for poor-fit niches via:

    niches:
      gaming:
        affiliate_enabled: false
        products: [...]

This test pins the contract: ``affiliate_enabled: false`` skips the
whole stage; default (omitted or true) preserves current behavior.
"""

from __future__ import annotations

from unittest.mock import patch

from genlab_core.monetization.affiliate_matcher import AffiliateMatch


def _stories(n: int = 3) -> list[dict]:
    return [
        {"title": f"Story {i}", "summary": f"summary {i}", "_candidate_id": f"bp_{i}"}
        for i in range(n)
    ]


def _catalog_with_flag(*, flag_value):
    """Build a minimal valid catalog with affiliate_enabled per niche."""
    base = {
        "settings": {
            "max_affiliate_posts_per_day": 3,
            "default_network_priority": ["amazon"],
            "disclosure_text": {},
        },
        "niches": {
            "gaming": {
                "products": [
                    {
                        "name": "Test Product",
                        "keywords": ["story", "summary"],
                        "networks": {
                            "amazon": {
                                "url": "https://amazon.in/dp/X?tag=test",
                                "commission_pct": 3.0,
                            }
                        },
                        "enabled": True,
                    }
                ],
            }
        },
    }
    if flag_value is not None:
        base["niches"]["gaming"]["affiliate_enabled"] = flag_value
    return base


class TestPerNicheAffiliateDisable:
    def test_disabled_niche_skips_all_stories(self):
        """Pin: affiliate_enabled=false → no story gets an affiliate."""
        catalog = _catalog_with_flag(flag_value=False)
        stage = AffiliateMatch()
        context = {"niche_id": "gaming", "stories": _stories(3)}
        with patch(
            "genlab_core.monetization.affiliate_matcher._load_catalog",
            return_value=catalog,
        ):
            result = stage.execute(context)

        # No story has affiliate fields written
        for story in result["stories"]:
            assert "affiliate_product" not in story
            assert "affiliate_url" not in story
        # Run stats reflect the disable
        stats = result["run_stats"]["affiliate"]
        assert stats["matched"] == 0
        assert stats["skipped"] == 3
        assert stats.get("disabled_by_niche") is True

    def test_enabled_niche_matches_normally(self):
        """Pin: affiliate_enabled=true (default) → matching runs."""
        catalog = _catalog_with_flag(flag_value=True)
        stage = AffiliateMatch()
        context = {"niche_id": "gaming", "stories": _stories(2)}
        with patch(
            "genlab_core.monetization.affiliate_matcher._load_catalog",
            return_value=catalog,
        ):
            result = stage.execute(context)
        # Should NOT short-circuit
        stats = result["run_stats"]["affiliate"]
        assert "disabled_by_niche" not in stats

    def test_flag_omitted_defaults_to_enabled(self):
        """Pin: backward compat — niches without the flag work as before
        (default true)."""
        catalog = _catalog_with_flag(flag_value=None)
        stage = AffiliateMatch()
        context = {"niche_id": "gaming", "stories": _stories(2)}
        with patch(
            "genlab_core.monetization.affiliate_matcher._load_catalog",
            return_value=catalog,
        ):
            result = stage.execute(context)
        stats = result["run_stats"]["affiliate"]
        assert "disabled_by_niche" not in stats

    def test_unknown_niche_doesnt_crash(self):
        """Pin: niche_id not in catalog → no crash, normal flow.
        Important: niche_cfg defaults to {} so the .get('affiliate_enabled', True)
        falls back to True (= run normally, find no products, skip)."""
        catalog = _catalog_with_flag(flag_value=False)  # disabled for 'gaming'
        stage = AffiliateMatch()
        context = {"niche_id": "unknown_niche", "stories": _stories(2)}
        with patch(
            "genlab_core.monetization.affiliate_matcher._load_catalog",
            return_value=catalog,
        ):
            result = stage.execute(context)
        # Should NOT short-circuit (the disable is per-niche, not global)
        stats = result["run_stats"]["affiliate"]
        assert "disabled_by_niche" not in stats
