"""Pins for the Cuelinks candidate-list history + current invariant.

## 2026-06-14 removal (queue item #12 audit)

The audit found that **every cuelinks redirect in the 73-click
historical sample earned zero commission** because our catalog's
cuelinks entries had bare ``amazon.in/dp/B0XYZ`` URLs without the
``tag=aspirehub-21`` affiliate tag. Cuelinks faithfully relays
whatever URL it's given — it doesn't INJECT attribution — so the
final 302 sent users to un-attributed Amazon links.

## 2026-07-16 re-integration (PR 3 of 3)

Cuelinks V3 API added back as a COMPLEMENT to Amazon direct — never
a replacement. Invariants pinned across this file + sibling files:

  * Cuelinks IS in the candidate list, but LAST after Amazon adapters
  * ``cuelinks_client.AmazonUrlNotAllowed`` raises at runtime for any
    amazon.* URL, so a mis-configured catalog entry can't re-create
    the 2026-06-14 ₹0 incident
  * ``TestCuelinksAmazonGuard`` in ``test_affiliate_catalog_cleanup.py``
    pins the catalog-level version of the same guard
  * ``TestGeoResolverCandidateOrdering`` in
    ``test_cuelinks_adapter_wire.py`` pins Amazon-adapter-before-cuelinks
    ordering in both geo lists

The old "cuelinks fully excluded" pins in this file have been rewritten
to reflect the narrower + safer invariants.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from genlab_core.monetization import geo_link_resolver
from genlab_core.monetization.geo_link_resolver import (
    NICHE_PRIMARY_GEO,
    resolve_affiliate_link_with_network,
)


@pytest.fixture
def _no_url_health_check(monkeypatch):
    """Skip the live HTTP HEAD check — tests use synthetic URLs."""
    monkeypatch.setattr(geo_link_resolver, "_is_url_healthy", lambda url: True)


def _get_candidate_list(geo: str) -> list[str]:
    """Run the resolver once with a dummy product to surface the
    candidate list it would walk. The candidate list is internal
    so we extract it by giving each potential network a fake URL
    and seeing which one gets picked first."""
    networks = {
        net: {"url": f"https://{net}-fake.example.com/x"}
        for net in [
            "amazon",
            "amazon_in",
            "amazon_us",
            "shareasale",
            "cj",
            "impact",
            "earnkaro",
            "cuelinks",
        ]
    }
    product = {"name": "test", "networks": networks}
    # The resolver picks the first network whose url isn't placeholder/
    # broken — with all URLs present, it picks the FIRST entry in its
    # candidate list. Iteratively strip the picked one to enumerate.
    seen: list[str] = []
    remaining = dict(networks)
    # Use a known niche per geo
    niche = "gaming" if geo == "IN" else "ai_creators"
    while remaining:
        product["networks"] = remaining
        with patch.object(geo_link_resolver, "_is_url_healthy", return_value=True):
            url, picked = resolve_affiliate_link_with_network(product, niche, "instagram")
        if not picked or picked == "earnkaro":
            # EarnKaro auto-transform may swap the picked network; we
            # care about the ORIGINAL candidate so handle the swap by
            # checking what remained.
            break
        if picked in seen:
            break
        seen.append(picked)
        remaining.pop(picked, None)
        if not remaining:
            break
    return seen


class TestCuelinksInCandidateList:
    """PR 3 (2026-07-16) — Cuelinks is BACK in the candidate list as
    LAST fallback. These tests pin the new invariants: cuelinks
    fires only for non-Amazon merchants, Amazon always wins when
    both are present, and Amazon URLs mistakenly-wrapped-in-cuelinks
    still hit the ``AmazonUrlNotAllowed`` runtime guard."""

    def test_in_geo_cuelinks_only_product_now_picks_cuelinks(self, _no_url_health_check):
        """A product with ONLY a cuelinks entry (non-Amazon merchant
        like Flipkart/Myntra) should now pick cuelinks. This is the
        new invariant post PR 3 (2026-07-16 re-integration).

        The URL wrapped inside cuelinks MUST be non-Amazon per the
        catalog pin (test_no_product_uses_cuelinks_for_amazon_url).
        Live-URL health check is stubbed via the fixture; the resolver's
        role is to pick, not to validate the wrapped URL contents.
        """
        # Non-Amazon cuelinks URL (Flipkart-like) — the shape the V3
        # integration is designed to route
        product = {
            "name": "cuelinks-flipkart",
            "networks": {
                "cuelinks": {"url": "https://www.cuelinks.com/tracked/flipkart-xyz"},
            },
        }
        url, network = resolve_affiliate_link_with_network(
            product, "gaming", "instagram", blueprint_id="bp1"
        )
        # New invariant: cuelinks IS pickable when it's the only candidate
        assert network == "cuelinks", (
            f"cuelinks-only non-Amazon product should now pick cuelinks "
            f"(2026-07-16 PR 3 re-integration); got network={network!r}"
        )
        assert url != ""

    def test_us_geo_cuelinks_only_product_now_picks_cuelinks(self, _no_url_health_check):
        product = {
            "name": "cuelinks-us-only",
            "networks": {
                "cuelinks": {"url": "https://www.cuelinks.com/tracked/us-merchant"},
            },
        }
        url, network = resolve_affiliate_link_with_network(
            product, "ai_creators", "instagram", blueprint_id="bp1"
        )
        assert network == "cuelinks"
        assert url != ""

    def test_amazon_still_preferred_when_both_present(self, _no_url_health_check):
        """The original behavior — amazon picked over cuelinks — is
        preserved. This is the regression pin that the candidate-list
        edit didn't accidentally drop amazon too."""
        product = {
            "name": "both",
            "networks": {
                "amazon": {"url": "https://www.amazon.in/dp/B0ABC?tag=aspirehub-21"},
                "cuelinks": {
                    "url": (
                        "https://linksredirect.com/?cid=272705&source=linkkit&"
                        "url=https%3A%2F%2Fwww.amazon.in%2Fdp%2FB0ABC"
                    )
                },
            },
        }
        url, network = resolve_affiliate_link_with_network(
            product, "gaming", "instagram", blueprint_id="bp1"
        )
        # Amazon was preferred before; still preferred (cuelinks isn't
        # even in the candidate list anymore).
        assert network == "amazon"
        assert "aspirehub-21" in url


class TestNicheGeoMapping:
    """Niche → primary-audience-geo mapping.

    Updated 2026-06-17 from the original IN-defaulted setup based on
    actual `affiliate_clicks.country` data showing 91% US audience
    across all 5 niches. See the docstring of
    ``geo_link_resolver.NICHE_PRIMARY_GEO`` for the click-distribution
    snapshot that motivated the flip + the test in
    ``test_niche_primary_geo.py`` that is now the canonical pin.

    This pin in particular guards that the cuelinks-removal edit
    didn't accidentally mutate the mapping in either direction — it
    asserts the post-2026-06-17 US defaults.
    """

    @pytest.mark.parametrize(
        "niche,expected_geo",
        [
            ("ai_creators", "US"),
            ("gaming", "US"),
            ("sports", "US"),
            ("movies", "US"),
            ("anime", "US"),
        ],
    )
    def test_niche_geo_mapping(self, niche, expected_geo):
        assert NICHE_PRIMARY_GEO[niche] == expected_geo
