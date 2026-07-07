"""Pin the 2026-07-07 slugify_product_name (L3 PR 2).

The canonical JOIN key that makes ``affiliate_clicks.product_id`` and
``blueprints.product_slug`` join-able. Without this, click→blueprint
attribution is architecturally impossible.

If this pin regresses (or the slug format drifts from what the
bio-link hub at review.aspirehub.ai/links/go/<slug> emits), 30 days of
click data will fail to attribute (audit found this state on
2026-07-07 before this fix landed).
"""

from __future__ import annotations

import pytest
from genlab_core.monetization.affiliate_matcher import slugify_product_name


class TestSlugifyProductName:
    """Canonical slug behavior across the catalog products actually in use."""

    @pytest.mark.parametrize(
        "input_name,expected",
        [
            # ai_creators catalog
            ("NVIDIA RTX 4090", "nvidia-rtx-4090"),
            ("Claude Pro Subscription", "claude-pro-subscription"),
            ("AI Course Bundle", "ai-course-bundle"),
            ("Ring Light", "ring-light"),
            ("Portable Monitor", "portable-monitor"),
            # gaming catalog
            ("PS5 Console", "ps5-console"),
            ("Xbox Game Pass Ultimate", "xbox-game-pass-ultimate"),
            ("Steam Deck", "steam-deck"),
            ("Razer Gaming Headset", "razer-gaming-headset"),
            # movies catalog
            ("Amazon Prime Video", "amazon-prime-video"),
            ("JBL Cinema Soundbar", "jbl-cinema-soundbar"),
            ("4K Projector", "4k-projector"),
            ("Blu-ray Player", "blu-ray-player"),  # existing hyphen preserved
            # anime catalog
            ("One Piece Manga Box Set", "one-piece-manga-box-set"),
            ("Anime Figure Collection", "anime-figure-collection"),
            ("Funko Pop Anime", "funko-pop-anime"),
            # sports catalog
            ("NBA League Pass", "nba-league-pass"),
            ("Nike Running Shoes", "nike-running-shoes"),
        ],
    )
    def test_catalog_names_produce_expected_slugs(self, input_name, expected):
        """Each entry must produce the slug the bio-link hub already uses."""
        assert slugify_product_name(input_name) == expected

    def test_empty_string_returns_empty(self):
        assert slugify_product_name("") == ""

    def test_none_returns_empty(self):
        # Defensive: the caller may pass None if catalog missed a name field
        assert slugify_product_name(None) == ""

    def test_unicode_characters_stripped(self):
        """Slug rule (2) drops chars outside ``[a-z0-9\\s-]``. Confirms
        the LLM-hallucination-era product names with emoji or Unicode
        don't produce weird slugs."""
        assert slugify_product_name("Café ☕ Special") == "caf-special"

    def test_multiple_spaces_collapse_to_single_hyphen(self):
        assert slugify_product_name("Product   With    Extra   Spaces") == (
            "product-with-extra-spaces"
        )

    def test_underscores_treated_as_separator(self):
        assert slugify_product_name("product_with_underscores") == ("product-with-underscores")

    def test_leading_trailing_hyphens_stripped(self):
        """Sanitised input might produce ``-slug-`` — strip both ends."""
        assert slugify_product_name(" - -leading trailing- - ") == "leading-trailing"

    def test_all_uppercase_lowercased(self):
        assert slugify_product_name("PS5") == "ps5"
        assert slugify_product_name("NBA") == "nba"

    def test_double_hyphens_collapse(self):
        """Different separator sources can produce doubled hyphens; de-double."""
        assert slugify_product_name("Blu -- ray Player") == "blu-ray-player"

    def test_numeric_only_preserved(self):
        """Slug must preserve digits — used in things like '4090', 'PS5'."""
        assert slugify_product_name("Product 2026") == "product-2026"
