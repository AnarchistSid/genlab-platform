"""Tests for the affiliate product matching pipeline stage.

Covers keyword matching edge cases, network selection, niche isolation,
seasonal filtering, CTA/hashtag stripping, and the end-to-end execute() path.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from genlab_core.monetization.affiliate_matcher import (
    _keyword_hits,
    match_product,
    select_best_network,
    AffiliateMatch,
)


# ---------------------------------------------------------------------------
# Shared helpers & fixtures
# ---------------------------------------------------------------------------

def _make_catalog(niche_id: str = "gaming", products: list | None = None) -> dict:
    """Build a minimal affiliate catalog dict."""
    if products is None:
        products = [
            {
                "name": "PS5 Console",
                "keywords": ["ps5", "playstation", "console"],
                "networks": {
                    "amazon": {
                        "url": "https://www.amazon.in/dp/B0CY5QW186?tag=***REMOVED***",
                        "commission_pct": 4.0,
                    }
                },
            }
        ]
    return {"niches": {niche_id: {"products": products}}, "settings": {}}


def _make_multi_catalog() -> dict:
    """Catalog with products across multiple niches."""
    return {
        "niches": {
            "gaming": {
                "products": [
                    {
                        "name": "PS5 Console",
                        "keywords": ["ps5", "playstation", "console"],
                        "networks": {
                            "amazon": {
                                "url": "https://www.amazon.in/dp/PS5?tag=***REMOVED***",
                                "commission_pct": 4.0,
                            }
                        },
                    },
                    {
                        "name": "RTX 4090",
                        "keywords": ["nvidia", "rtx", "4090", "gpu"],
                        "networks": {
                            "amazon": {
                                "url": "https://www.amazon.in/dp/RTX?tag=***REMOVED***",
                                "commission_pct": 4.0,
                            }
                        },
                    },
                ]
            },
            "movies": {
                "products": [
                    {
                        "name": "4K Blu-ray Player",
                        "keywords": ["blu-ray", "4k", "player"],
                        "networks": {
                            "amazon": {
                                "url": "https://www.amazon.in/dp/BLU?tag=***REMOVED***",
                                "commission_pct": 3.0,
                            }
                        },
                    }
                ]
            },
        },
        "settings": {"max_affiliate_posts_per_day": 3},
    }


# ---------------------------------------------------------------------------
# a. Word-boundary matching — no substring false positives
# ---------------------------------------------------------------------------


class TestWordBoundaryMatching:
    """_keyword_hits uses \\b boundaries to prevent partial-word matches."""

    def test_clips_does_not_match_ips(self):
        """'clips' should NOT match keyword 'ips' — not a word boundary match."""
        assert _keyword_hits(["ips"], "best video clips of the year") == 0

    def test_debating_does_not_match_bat(self):
        """'debating' should NOT match keyword 'bat' — embedded substring."""
        assert _keyword_hits(["bat"], "they are debating the topic") == 0

    def test_called_does_not_match_led(self):
        """'called' should NOT match keyword 'led' — trailing substring."""
        assert _keyword_hits(["led"], "she called him out") == 0

    def test_exact_word_does_match(self):
        """Exact word-boundary match should work normally."""
        assert _keyword_hits(["ps5"], "the ps5 is amazing") == 1

    def test_hyphenated_boundary(self):
        """Keywords at hyphen boundaries should match (\\b treats - as boundary)."""
        assert _keyword_hits(["ray"], "blu-ray player") == 1

    def test_keyword_at_start_of_string(self):
        """Keyword at very start of text matches."""
        assert _keyword_hits(["nvidia"], "nvidia announced new gpu") == 1

    def test_keyword_at_end_of_string(self):
        """Keyword at very end of text matches."""
        assert _keyword_hits(["gpu"], "best nvidia gpu") == 1

    def test_multiple_keywords_counted(self):
        """Multiple matching keywords each count as a hit."""
        assert _keyword_hits(["ps5", "console", "playstation"], "ps5 console by playstation") == 3


# ---------------------------------------------------------------------------
# b. Integer keywords — YAML auto-parsed ints cast to str
# ---------------------------------------------------------------------------


class TestIntegerKeywords:
    """YAML parses bare numbers like 4090 as int; _keyword_hits casts to str."""

    def test_int_keyword_matches(self):
        """Integer keyword 4090 should match 'RTX 4090' text."""
        assert _keyword_hits([4090], "the new rtx 4090 is powerful") == 1

    def test_int_keyword_word_boundary(self):
        """Integer 90 should NOT match '4090' — word boundary prevents it."""
        assert _keyword_hits([90], "the rtx 4090 is great") == 0

    def test_mixed_int_and_str_keywords(self):
        """Mix of int and str keywords all work correctly."""
        assert _keyword_hits(["rtx", 4090, "nvidia"], "nvidia rtx 4090 review") == 3


# ---------------------------------------------------------------------------
# c. None keywords — safely skipped
# ---------------------------------------------------------------------------


class TestNoneKeywords:
    def test_none_keyword_skipped(self):
        """A None keyword should be safely skipped, not raise an error."""
        assert _keyword_hits([None, "ps5"], "buy a ps5 today") == 1

    def test_all_none_keywords(self):
        """A list of all None keywords returns 0 hits."""
        assert _keyword_hits([None, None], "any text here") == 0


# ---------------------------------------------------------------------------
# d. Empty string keywords — skipped
# ---------------------------------------------------------------------------


class TestEmptyStringKeywords:
    def test_empty_string_skipped(self):
        """An empty string keyword should be skipped."""
        assert _keyword_hits(["", "ps5"], "buy a ps5 today") == 1

    def test_whitespace_only_skipped(self):
        """A whitespace-only keyword should be treated as empty and skipped."""
        assert _keyword_hits(["   ", "ps5"], "ps5 gaming") == 1

    def test_all_empty_keywords(self):
        """A list of all empty keywords returns 0 hits."""
        assert _keyword_hits(["", "", "   "], "any text") == 0


# ---------------------------------------------------------------------------
# e. Hashtag stripping — #Cinema should not cause keyword match on "cinema"
# ---------------------------------------------------------------------------


class TestHashtagStripping:
    def test_hashtag_text_stripped_before_matching(self):
        """Text containing '#Cinema' should NOT match keyword 'cinema'."""
        catalog = _make_catalog(
            "movies",
            [
                {
                    "name": "Cinema Pass",
                    "keywords": ["cinema"],
                    "networks": {
                        "amazon": {"url": "https://example-real.com/cinema", "commission_pct": 2.0}
                    },
                }
            ],
        )
        # Only hashtag reference, no plain-text "cinema"
        text = "Amazing scene today #Cinema #Trending"
        result = match_product(text, "movies", catalog, seasonal_config={})
        assert result is None, "Hashtag #Cinema should be stripped; keyword 'cinema' should not match"

    def test_plain_text_still_matches_after_hashtag_strip(self):
        """Plain text 'cinema' (not a hashtag) should still match."""
        catalog = _make_catalog(
            "movies",
            [
                {
                    "name": "Cinema Pass",
                    "keywords": ["cinema", "movie"],
                    "networks": {
                        "amazon": {"url": "https://example-real.com/cinema", "commission_pct": 2.0}
                    },
                }
            ],
        )
        text = "Best cinema experience and movie of the year"
        result = match_product(text, "movies", catalog, seasonal_config={})
        assert result is not None
        assert result["name"] == "Cinema Pass"


# ---------------------------------------------------------------------------
# f. CTA stripping — existing affiliate CTAs should not self-match
# ---------------------------------------------------------------------------


class TestCTAStripping:
    def test_cta_text_stripped(self):
        """'🔗 PS5 Console — link in bio' should not match keywords from the CTA."""
        catalog = _make_catalog(
            "gaming",
            [
                {
                    "name": "PS5 Console",
                    "keywords": ["ps5", "console"],
                    "networks": {
                        "amazon": {"url": "https://amzn.to/ps5", "commission_pct": 4.0}
                    },
                }
            ],
        )
        # The ONLY mentions of ps5/console are inside the CTA
        text = "Great gameplay footage today 🔗 PS5 Console — link in bio"
        result = match_product(text, "gaming", catalog, seasonal_config={})
        assert result is None, "CTA text should be stripped; no match from self-referencing CTA"

    def test_real_content_plus_cta_still_matches(self):
        """If content mentions the product AND has a CTA, the content match still works."""
        catalog = _make_catalog(
            "gaming",
            [
                {
                    "name": "PS5 Console",
                    "keywords": ["ps5", "console"],
                    "networks": {
                        "amazon": {"url": "https://amzn.to/ps5", "commission_pct": 4.0}
                    },
                }
            ],
        )
        text = "The PS5 console is incredible 🔗 PS5 Console — link in bio"
        result = match_product(text, "gaming", catalog, seasonal_config={})
        assert result is not None
        assert result["name"] == "PS5 Console"


# ---------------------------------------------------------------------------
# g. Seasonal niche filter — seasonal product targeted at one niche
# ---------------------------------------------------------------------------


class TestSeasonalNicheFilter:
    def test_seasonal_product_wrong_niche_is_skipped(self):
        """Seasonal product with niche_id='gaming' should NOT match in niche_id='anime'."""
        seasonal_config = {
            "events": [
                {
                    "name": "Test Sale",
                    "start": "2020-01-01",
                    "end": "2030-12-31",
                    "products": [
                        {
                            "name": "Gaming Headset",
                            "keywords": ["headset", "gaming", "audio"],
                            "niche_id": "gaming",
                            "networks": {
                                "amazon": {"url": "https://amzn.to/headset", "commission_pct": 5.0}
                            },
                        }
                    ],
                }
            ]
        }
        # Static catalog empty for anime
        catalog = _make_catalog("anime", [])
        text = "amazing headset for gaming and audio setup"
        result = match_product(text, "anime", catalog, seasonal_config=seasonal_config)
        assert result is None, "Seasonal product targeted at 'gaming' must not match in 'anime' niche"

    def test_seasonal_product_correct_niche_matches(self):
        """Seasonal product with niche_id='gaming' matches when niche_id='gaming'."""
        seasonal_config = {
            "events": [
                {
                    "name": "Test Sale",
                    "start": "2020-01-01",
                    "end": "2030-12-31",
                    "products": [
                        {
                            "name": "Gaming Headset",
                            "keywords": ["headset", "gaming", "audio"],
                            "niche_id": "gaming",
                            "networks": {
                                "amazon": {"url": "https://amzn.to/headset", "commission_pct": 5.0}
                            },
                        }
                    ],
                }
            ]
        }
        catalog = _make_catalog("gaming", [])
        text = "amazing headset for gaming and audio setup"
        result = match_product(text, "gaming", catalog, seasonal_config=seasonal_config)
        assert result is not None
        assert result["name"] == "Gaming Headset"


# ---------------------------------------------------------------------------
# h. Empty catalog — returns None
# ---------------------------------------------------------------------------


class TestEmptyCatalog:
    def test_empty_catalog_returns_none(self):
        """An empty catalog with no niche products returns None."""
        catalog = {"niches": {}, "settings": {}}
        result = match_product("some gaming text", "gaming", catalog, seasonal_config={})
        assert result is None

    def test_niche_missing_from_catalog(self):
        """If the requested niche_id is not in the catalog, return None."""
        catalog = _make_catalog("movies")  # only movies
        result = match_product("some gaming text", "gaming", catalog, seasonal_config={})
        assert result is None

    def test_niche_present_but_no_products(self):
        """Niche exists but products list is empty -> None."""
        catalog = _make_catalog("gaming", products=[])
        result = match_product("ps5 gaming", "gaming", catalog, seasonal_config={})
        assert result is None


# ---------------------------------------------------------------------------
# i. Niche isolation — gaming products must NOT match in movies niche
# ---------------------------------------------------------------------------


class TestNicheIsolation:
    def test_gaming_product_not_in_movies(self):
        """Gaming products should NOT match when niche_id='movies'."""
        catalog = _make_multi_catalog()
        text = "the ps5 playstation console is amazing for gaming"
        result = match_product(text, "movies", catalog, seasonal_config={})
        # Movies niche has 4K Blu-ray, not PS5 — so PS5 keywords should not match
        if result is not None:
            assert result["name"] != "PS5 Console", "PS5 (gaming) must not match in movies niche"

    def test_movies_product_not_in_gaming(self):
        """Movies products should NOT match when niche_id='gaming'."""
        catalog = _make_multi_catalog()
        text = "the best 4k blu-ray player for your home"
        result = match_product(text, "gaming", catalog, seasonal_config={})
        # Gaming niche has PS5 and RTX, not Blu-ray
        if result is not None:
            assert result["name"] != "4K Blu-ray Player", "Blu-ray (movies) must not match in gaming"


# ---------------------------------------------------------------------------
# j. Multi-keyword priority — more hits wins
# ---------------------------------------------------------------------------


class TestMultiKeywordPriority:
    def test_product_with_more_hits_wins(self):
        """Product matching 3 keywords should beat one matching only 1."""
        catalog = _make_catalog(
            "gaming",
            [
                {
                    "name": "Generic Controller",
                    "keywords": ["controller"],
                    "networks": {
                        "amazon": {"url": "https://amzn.to/ctrl", "commission_pct": 3.0}
                    },
                },
                {
                    "name": "PS5 DualSense Controller",
                    "keywords": ["ps5", "dualsense", "controller"],
                    "networks": {
                        "amazon": {"url": "https://amzn.to/dualsense", "commission_pct": 4.0}
                    },
                },
            ],
        )
        text = "the ps5 dualsense controller is the best"
        result = match_product(text, "gaming", catalog, seasonal_config={})
        assert result is not None
        assert result["name"] == "PS5 DualSense Controller"

    def test_single_hit_still_matches_if_no_competition(self):
        """A product with 1 keyword hit still matches when it's the only candidate."""
        catalog = _make_catalog(
            "gaming",
            [
                {
                    "name": "Gaming Mouse",
                    "keywords": ["mouse", "logitech"],
                    "networks": {
                        "amazon": {"url": "https://amzn.to/mouse", "commission_pct": 3.0}
                    },
                }
            ],
        )
        text = "this mouse is incredible for fps games"
        result = match_product(text, "gaming", catalog, seasonal_config={})
        assert result is not None
        assert result["name"] == "Gaming Mouse"


# ---------------------------------------------------------------------------
# k. select_best_network — skips example.com, picks highest commission
# ---------------------------------------------------------------------------


class TestSelectBestNetwork:
    def test_skip_example_com_urls(self):
        """Networks with example.com URLs should be skipped."""
        product = {
            "networks": {
                "placeholder": {
                    "url": "https://example.com/placeholder",
                    "commission_pct": 10.0,
                },
                "real_network": {
                    "url": "https://amzn.to/real",
                    "commission_pct": 4.0,
                },
            }
        }
        name, url, commission = select_best_network(product)
        assert name == "real_network"
        assert url == "https://amzn.to/real"
        assert commission == 4.0

    def test_picks_highest_commission(self):
        """When multiple valid networks exist, pick the one with highest commission."""
        product = {
            "networks": {
                "amazon": {
                    "url": "https://amzn.to/product",
                    "commission_pct": 4.0,
                },
                "cuelinks": {
                    "url": "https://cuelinks.com/product",
                    "commission_pct": 7.0,
                },
                "earnkaro": {
                    "url": "https://earnkaro.com/product",
                    "commission_pct": 5.0,
                },
            }
        }
        name, url, commission = select_best_network(product)
        assert name == "cuelinks"
        assert commission == 7.0

    def test_empty_networks_returns_defaults(self):
        """Product with no networks returns empty strings and 0.0 commission."""
        product = {"networks": {}}
        name, url, commission = select_best_network(product)
        assert name == ""
        assert url == ""
        assert commission == 0.0

    def test_no_networks_key(self):
        """Product missing the networks key entirely returns defaults."""
        product = {}
        name, url, commission = select_best_network(product)
        assert name == ""
        assert url == ""
        assert commission == 0.0

    def test_all_example_com_returns_empty(self):
        """When all networks are example.com, returns empty (no valid network)."""
        product = {
            "networks": {
                "a": {"url": "https://example.com/a", "commission_pct": 10.0},
                "b": {"url": "https://example.com/b", "commission_pct": 8.0},
            }
        }
        name, url, commission = select_best_network(product)
        assert name == ""
        assert url == ""

    def test_empty_url_skipped(self):
        """Network with empty string URL is skipped."""
        product = {
            "networks": {
                "empty": {"url": "", "commission_pct": 10.0},
                "valid": {"url": "https://amzn.to/real", "commission_pct": 3.0},
            }
        }
        name, url, commission = select_best_network(product)
        assert name == "valid"


# ---------------------------------------------------------------------------
# l. AffiliateMatch.execute() — end-to-end with mock catalog
# ---------------------------------------------------------------------------


class TestAffiliateMatchExecute:
    """Test the full pipeline stage execute() method."""

    def _mock_catalog(self) -> dict:
        return {
            "niches": {
                "gaming": {
                    "products": [
                        {
                            "name": "PS5 Console",
                            "keywords": ["ps5", "playstation", "console"],
                            "networks": {
                                "amazon": {
                                    "url": "https://amzn.to/ps5",
                                    "commission_pct": 4.0,
                                }
                            },
                        },
                        {
                            "name": "Xbox Controller",
                            "keywords": ["xbox", "controller"],
                            "networks": {
                                "amazon": {
                                    "url": "https://amzn.to/xbox",
                                    "commission_pct": 3.5,
                                }
                            },
                        },
                    ]
                }
            },
            "settings": {
                "max_affiliate_posts_per_day": 2,
                "disclosure_text": {
                    "instagram": "#affiliate",
                    "youtube": "Contains affiliate links.",
                },
            },
        }

    @patch("genlab_core.monetization.affiliate_matcher._load_catalog")
    @patch("genlab_core.monetization.affiliate_matcher.load_seasonal_config")
    def test_matching_story_gets_affiliate_fields(self, mock_seasonal, mock_catalog):
        mock_catalog.return_value = self._mock_catalog()
        mock_seasonal.return_value = {}

        stage = AffiliateMatch()
        context = {
            "niche_id": "gaming",
            "stories": [
                {
                    "title": "PS5 Review",
                    "content": {
                        "hook": "The PS5 playstation console is here",
                        "instagram": {"caption": "Amazing console gameplay"},
                    },
                }
            ],
        }
        result = stage.execute(context)
        story = result["stories"][0]
        assert story["affiliate_product"] == "PS5 Console"
        assert story["affiliate_url"] == "https://amzn.to/ps5"
        assert story["affiliate_network"] == "amazon"
        assert story["affiliate_commission_pct"] == 4.0
        assert "link in bio" in story["affiliate_cta"]
        assert result["run_stats"]["affiliate"]["matched"] == 1

    @patch("genlab_core.monetization.affiliate_matcher._load_catalog")
    @patch("genlab_core.monetization.affiliate_matcher.load_seasonal_config")
    def test_no_match_leaves_story_unchanged(self, mock_seasonal, mock_catalog):
        mock_catalog.return_value = self._mock_catalog()
        mock_seasonal.return_value = {}

        stage = AffiliateMatch()
        context = {
            "niche_id": "gaming",
            "stories": [
                {
                    "title": "Random News",
                    "content": {
                        "hook": "Something completely unrelated happened",
                        "instagram": {"caption": "No product keywords here"},
                    },
                }
            ],
        }
        result = stage.execute(context)
        story = result["stories"][0]
        assert "affiliate_product" not in story
        assert result["run_stats"]["affiliate"]["skipped"] == 1

    @patch("genlab_core.monetization.affiliate_matcher._load_catalog")
    @patch("genlab_core.monetization.affiliate_matcher.load_seasonal_config")
    def test_daily_cap_enforced(self, mock_seasonal, mock_catalog):
        mock_catalog.return_value = self._mock_catalog()
        mock_seasonal.return_value = {}

        stage = AffiliateMatch()
        # 3 stories, but cap is 2
        context = {
            "niche_id": "gaming",
            "stories": [
                {
                    "title": "PS5 Story 1",
                    "content": {"hook": "PS5 playstation console review"},
                },
                {
                    "title": "Xbox Story",
                    "content": {"hook": "Xbox controller review"},
                },
                {
                    "title": "PS5 Story 2",
                    "content": {"hook": "Another ps5 console review"},
                },
            ],
        }
        result = stage.execute(context)
        stats = result["run_stats"]["affiliate"]
        assert stats["matched"] == 2
        assert stats["cap_enforced"] == 1

    @patch("genlab_core.monetization.affiliate_matcher._load_catalog")
    @patch("genlab_core.monetization.affiliate_matcher.load_seasonal_config")
    def test_empty_stories_list(self, mock_seasonal, mock_catalog):
        mock_catalog.return_value = self._mock_catalog()
        mock_seasonal.return_value = {}

        stage = AffiliateMatch()
        context = {"niche_id": "gaming", "stories": []}
        result = stage.execute(context)
        assert result["run_stats"]["affiliate"]["matched"] == 0
        assert result["run_stats"]["affiliate"]["skipped"] == 0

    @patch("genlab_core.monetization.affiliate_matcher._load_catalog")
    @patch("genlab_core.monetization.affiliate_matcher.load_seasonal_config")
    def test_catalog_load_failure_graceful(self, mock_seasonal, mock_catalog):
        mock_catalog.side_effect = FileNotFoundError("catalog not found")
        mock_seasonal.return_value = {}

        stage = AffiliateMatch()
        context = {
            "niche_id": "gaming",
            "stories": [{"title": "Test", "content": {"hook": "ps5 console"}}],
        }
        result = stage.execute(context)
        stats = result["run_stats"]["affiliate"]
        assert stats["matched"] == 0
        assert stats["skipped"] == 1
        assert "error" in stats

    @patch("genlab_core.monetization.affiliate_matcher._load_catalog")
    @patch("genlab_core.monetization.affiliate_matcher.load_seasonal_config")
    def test_story_with_string_content_handled(self, mock_seasonal, mock_catalog):
        """Stories where content is a JSON string instead of a dict should be handled."""
        mock_catalog.return_value = self._mock_catalog()
        mock_seasonal.return_value = {}

        stage = AffiliateMatch()
        import json
        context = {
            "niche_id": "gaming",
            "stories": [
                {
                    "title": "PS5 Review",
                    "content": json.dumps({
                        "hook": "PS5 playstation console launch",
                        "instagram": {"caption": "gaming setup"},
                    }),
                }
            ],
        }
        result = stage.execute(context)
        story = result["stories"][0]
        assert story["affiliate_product"] == "PS5 Console"
        assert result["run_stats"]["affiliate"]["matched"] == 1

    @patch("genlab_core.monetization.affiliate_matcher._load_catalog")
    @patch("genlab_core.monetization.affiliate_matcher.load_seasonal_config")
    def test_product_with_no_valid_network_skipped(self, mock_seasonal, mock_catalog):
        """Product matches keywords but all networks are example.com -> skipped."""
        catalog = {
            "niches": {
                "gaming": {
                    "products": [
                        {
                            "name": "Broken Product",
                            "keywords": ["ps5", "console"],
                            "networks": {
                                "placeholder": {
                                    "url": "https://example.com/todo",
                                    "commission_pct": 5.0,
                                }
                            },
                        }
                    ]
                }
            },
            "settings": {},
        }
        mock_catalog.return_value = catalog
        mock_seasonal.return_value = {}

        stage = AffiliateMatch()
        context = {
            "niche_id": "gaming",
            "stories": [{"title": "PS5 Review", "content": {"hook": "ps5 console"}}],
        }
        result = stage.execute(context)
        assert result["run_stats"]["affiliate"]["matched"] == 0
        assert result["run_stats"]["affiliate"]["skipped"] == 1
