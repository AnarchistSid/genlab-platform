"""Pins for the 2026-06-14 cuelinks removal (queue item #12 audit).

The audit found that **every cuelinks redirect in the 73-click
historical sample earned zero commission**, because our catalog's
cuelinks entries had bare ``amazon.in/dp/B0XYZ`` URLs without the
``tag=aspirehub-21`` affiliate tag. Cuelinks faithfully relays
whatever URL it's given — it doesn't INJECT attribution — so the
final 302 sent users to un-attributed Amazon links.

Two fixes were considered:
  (a) Fix the catalog to embed our Amazon tag in cuelinks' inner URL
  (b) Remove cuelinks from the candidate list entirely

Option (b) won — cuelinks adds zero revenue value over a direct
Amazon link with the same tag, so the cuelinks hop is pure latency
+ third-party dependency for no gain.

This file pins the removal so a future PR that accidentally re-adds
cuelinks to the candidate list fails loudly with the audit context.
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


class TestCuelinksRemovedFromCandidates:
    def test_in_geo_candidate_list_excludes_cuelinks(self, _no_url_health_check):
        """The IN candidate list MUST NOT include cuelinks. Pin the
        2026-06-14 audit decision (zero commission earned across 73
        historical clicks)."""
        # Build product with ONLY cuelinks URL (no amazon)
        product = {
            "name": "cuelinks-only",
            "networks": {
                "cuelinks": {
                    "url": (
                        "https://linksredirect.com/?cid=272705&source=linkkit&"
                        "url=https%3A%2F%2Fwww.amazon.in%2Fdp%2FB0XYZ"
                    )
                },
            },
        }
        url, network = resolve_affiliate_link_with_network(
            product, "gaming", "instagram", blueprint_id="bp1"
        )
        # Pre-2026-06-14 this would have returned the cuelinks URL.
        # Post-2026-06-14 the candidate list doesn't include cuelinks,
        # so a cuelinks-only product → no URL.
        assert url == "", (
            "cuelinks-only product should return empty URL — cuelinks was "
            "removed from candidates on 2026-06-14 per the audit. If a "
            "future PR re-adds it, ensure the catalog's inner Amazon URLs "
            "carry tag=aspirehub-21 and update this pin with rationale."
        )
        assert network == ""

    def test_us_geo_candidate_list_excludes_cuelinks(self, _no_url_health_check):
        product = {
            "name": "cuelinks-only-us",
            "networks": {
                "cuelinks": {
                    "url": (
                        "https://linksredirect.com/?cid=272705&source=linkkit&"
                        "url=https%3A%2F%2Fwww.amazon.com%2Fdp%2FB0XYZ"
                    )
                },
            },
        }
        url, network = resolve_affiliate_link_with_network(
            product, "ai_creators", "instagram", blueprint_id="bp1"
        )
        assert url == ""
        assert network == ""

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
