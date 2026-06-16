"""Pin the NICHE_PRIMARY_GEO defaults so the 2026-06-17 audience-data
flip can't silently regress.

Background
----------
Until 2026-06-17 the map defaulted gaming/sports/movies/anime to "IN"
on the assumption their audience was Indian (set ~6 months earlier
when those niches were being seeded with India-focused content).
Trailing-90d `affiliate_clicks.country` data showed the audience was
overwhelmingly US:

  niche        US clicks  IN clicks  → US share
  ai_creators  10         5          67%
  anime        13         1          93%
  gaming       12         1          92%
  movies       27         0          100%
  sports       12         0          100%

Routing US-dominant traffic to amazon.in produced zero-attribution
clicks (US users can't buy on amazon.in). With all 5 niches now
defaulting to US, future clicks should attribute cleanly to
amazon.com.

If a niche's audience shifts toward India in the future, flip the
specific niche back here AND update the docstring comment above
with a fresh `affiliate_clicks.country` snapshot so the
justification is auditable.
"""

from __future__ import annotations

import pytest
from genlab_core.monetization.geo_link_resolver import NICHE_PRIMARY_GEO


class TestNichePrimaryGeo:
    def test_all_five_niches_present(self):
        """Pin the niche keys so accidental rename (e.g., 'ai_creators'
        → 'ai_news') trips this test before downstream geo routing
        silently defaults to US for the missing key."""
        assert set(NICHE_PRIMARY_GEO) == {
            "ai_creators",
            "anime",
            "gaming",
            "movies",
            "sports",
        }

    @pytest.mark.parametrize(
        "niche",
        ["ai_creators", "anime", "gaming", "movies", "sports"],
    )
    def test_all_niches_default_to_us_post_2026_06_17(self, niche):
        """All 5 niches default to US per the 2026-06-17 click-geography
        audit. If a niche's audience shifts back to India in future,
        flip that specific niche AND update the docstring with a fresh
        click-distribution snapshot — don't silently mutate this map.
        """
        assert NICHE_PRIMARY_GEO[niche] == "US", (
            f"Niche {niche!r} default geo changed without test update. "
            "If this is intentional, update the docstring with the "
            "click-distribution snapshot that justifies the change."
        )

    def test_values_are_valid_geo_codes(self):
        """Defensive — guards against typos like 'us' or 'United States'."""
        valid_geos = {"US", "IN"}
        for niche, geo in NICHE_PRIMARY_GEO.items():
            assert geo in valid_geos, (
                f"Niche {niche!r} has invalid geo {geo!r}; must be one of {valid_geos}"
            )
