"""Pin tests for FetchAnimePromos summary synthesis.

QB-FIX-01 F4 anime blocker (2026-08-06): AniList/Jikan promo dicts
hardcoded `summary=""` before landing in stories. Writer's 40-char
thin-context floor rejected every anime pipeline run → 0 blueprints
from N stories. Fix: `_build_promo_summary()` populates from AniList
description (preferred) or synthesizes from title + genres + studios.
"""

from __future__ import annotations

from genlab_core.pipeline.stages.fetch_anime_promos import _build_promo_summary


class TestBuildPromoSummary:
    def test_anilist_description_used_when_long_enough(self):
        p = {
            "title": "Frieren",
            "source": "anilist",
            "description": (
                "The elf mage Frieren travels through the world she once "
                "fought to save, meeting new friends and reflecting on the "
                "companions she lost."
            ),
            "genres": ["Adventure", "Drama"],
            "studios": ["Madhouse"],
        }
        summary = _build_promo_summary(p)
        assert summary.startswith("The elf mage Frieren")
        assert len(summary) >= 40

    def test_anilist_html_tags_stripped_upstream_but_short_desc_synthesizes(self):
        # If a fetcher passes through a very short/cleaned string
        # under the floor, synthesis fallback fires.
        p = {
            "title": "Chainsaw Man",
            "source": "anilist",
            "description": "TBA",
            "genres": ["Action", "Horror"],
            "studios": ["MAPPA"],
        }
        summary = _build_promo_summary(p)
        assert summary != "TBA"
        assert len(summary) >= 40
        assert "Chainsaw Man" in summary
        assert "MAPPA" in summary
        assert "Action" in summary

    def test_jikan_no_description_synthesizes_from_title(self):
        p = {
            "title": "Attack on Titan Final Season",
            "source": "jikan_promos",
        }
        summary = _build_promo_summary(p)
        assert len(summary) >= 40
        assert "Attack on Titan" in summary
        assert "trending anime PV" in summary

    def test_empty_promo_yields_empty_summary(self):
        # No title, no description, no metadata → summary stays empty,
        # writer correctly skips (no context to work with).
        p = {"title": "", "source": "anilist"}
        assert _build_promo_summary(p) == ""

    def test_seasonal_anime_trailer_label_used_for_anilist(self):
        p = {"title": "Solo Leveling S2", "source": "anilist"}
        summary = _build_promo_summary(p)
        assert "seasonal anime trailer" in summary
        assert "Solo Leveling" in summary

    def test_long_description_truncated(self):
        long_desc = "A very compelling and detailed synopsis. " * 30
        p = {"title": "Show", "source": "anilist", "description": long_desc}
        summary = _build_promo_summary(p)
        assert len(summary) <= 500
