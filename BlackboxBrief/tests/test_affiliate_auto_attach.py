"""BlackboxBrief source-tool affiliate detection tests.

Covers OPS #2 + #6 (2026-06-30) — auto-attaching the affiliate link
and "Made with <Tool> — <link>" disclosure when a BB reel's SOURCE
video explicitly uses a known AI tool (Runway, Sora, Midjourney).

The 6 cases below mirror the operator-decision matrix:
    1. Tool keyword in source video title → attach
    2. Mixed case match ("Sora") → still attach (case-insensitive)
    3. Generic AI content with no specific tool → NO attach
    4. Source matches multiple tools → first match wins, exactly one attach
    5. Niche != ai_creators → NO attach (catalog scoping is honored)
    6. Caption already has affiliate text → don't duplicate
"""

from __future__ import annotations

from typing import Any

from bb_strategies.affiliate_match import (
    apply_source_tool_disclosure,
    build_disclosure,
    detect_source_tool,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _runway_product() -> dict[str, Any]:
    return {
        "name": "Runway",
        "enabled": True,
        "source_tool": True,
        "tool_keyword": "runway",
        "disclosure_template": "Made with Runway — {url}",
        "keywords": ["runway", "runway ml"],
        "category": "tool",
        "niches": ["ai_creators"],
        "networks": {
            "direct": {
                "url": "https://runwayml.com/?via=genlab",
                "commission_pct": 0.0,
            }
        },
    }


def _sora_product() -> dict[str, Any]:
    return {
        "name": "Sora",
        "enabled": True,
        "source_tool": True,
        "tool_keyword": "sora",
        "disclosure_template": "Made with Sora — {url}",
        "keywords": ["sora"],
        "category": "tool",
        "niches": ["ai_creators"],
        "networks": {
            "direct": {
                "url": "https://openai.com/sora?via=genlab",
                "commission_pct": 0.0,
            }
        },
    }


def _midjourney_product() -> dict[str, Any]:
    return {
        "name": "Midjourney",
        "enabled": True,
        "source_tool": True,
        "tool_keyword": "midjourney",
        "disclosure_template": "Made with Midjourney — {url}",
        "keywords": ["midjourney"],
        "category": "tool",
        "niches": ["ai_creators"],
        "networks": {
            "direct": {
                "url": "https://midjourney.com/?ref=genlab",
                "commission_pct": 0.0,
            }
        },
    }


def _catalog(*, niche_id: str = "ai_creators", extra: list | None = None) -> dict:
    products = [_runway_product(), _sora_product(), _midjourney_product()]
    if extra:
        products.extend(extra)
    return {"niches": {niche_id: {"products": products}}, "settings": {}}


def _story(
    *,
    title: str = "",
    summary: str = "",
    tags: list[str] | None = None,
    channel_name: str = "",
) -> dict[str, Any]:
    return {
        "title": title,
        "summary": summary,
        "tags": tags or [],
        "channel_name": channel_name,
    }


def _empty_content() -> dict[str, Any]:
    """A platform-adaptation dict with a stub caption per platform."""
    return {
        "hook": "this is wild",
        "instagram": {"caption": "this is wild", "hashtags": ["#ai"]},
        "facebook": {"caption": "this is wild"},
        "tiktok": {"caption": "this is wild", "hashtags": ["#ai"]},
        "threads": {"caption": "this is wild"},
        "x_twitter": {"tweet": "this is wild"},
        "youtube": {"title": "wild ai demo"},
    }


# ---------------------------------------------------------------------------
# Test 1 — keyword in title → attach
# ---------------------------------------------------------------------------


class TestSourceTitleMatch:
    def test_runway_in_title_matches(self):
        story = _story(title="I used Runway to make this cinematic clip")
        product = detect_source_tool(story, catalog=_catalog())
        assert product is not None
        assert product["name"] == "Runway"

    def test_runway_in_title_attaches_disclosure_to_all_captions(self):
        story = _story(title="I used Runway to make this cinematic clip")
        content = _empty_content()
        attached = apply_source_tool_disclosure(content, story, catalog=_catalog())
        assert attached == "Runway"
        # Caption layered: original copy + the disclosure line.
        for key, field in (
            ("instagram", "caption"),
            ("facebook", "caption"),
            ("tiktok", "caption"),
            ("threads", "caption"),
            ("x_twitter", "tweet"),
        ):
            assert "Made with Runway" in content[key][field]
            assert "https://runwayml.com/?via=genlab" in content[key][field]
        # Tracking columns set on the upstream story dict for analytics.
        assert story["affiliate_product"] == "Runway"
        assert story["affiliate_url"] == "https://runwayml.com/?via=genlab"
        assert story["affiliate_network"] == "source_tool"
        assert story["affiliate_source"] == "source_tool_detection"
        # _skip_cta_inject signal prevents cta_engine from layering a
        # generic "🔗 Get Runway 👇" on top of the disclosure.
        assert story["_skip_cta_inject"] is True


# ---------------------------------------------------------------------------
# Test 2 — mixed-case match
# ---------------------------------------------------------------------------


class TestMixedCaseMatch:
    def test_sora_uppercase_in_title_matches(self):
        story = _story(title="Watch this insane Sora generation")
        product = detect_source_tool(story, catalog=_catalog())
        assert product is not None
        assert product["name"] == "Sora"

    def test_sora_in_description_matches(self):
        story = _story(
            title="incredible AI generation",
            summary="Made this with SORA last night — look at the lighting",
        )
        product = detect_source_tool(story, catalog=_catalog())
        assert product is not None
        assert product["name"] == "Sora"


# ---------------------------------------------------------------------------
# Test 3 — generic AI content → no attach
# ---------------------------------------------------------------------------


class TestGenericContentNoAttach:
    def test_generic_ai_tutorial_no_match(self):
        story = _story(
            title="AI tutorial for beginners",
            summary="Learn the basics of machine learning in 5 minutes",
            tags=["ai", "tutorial", "machine learning"],
        )
        assert detect_source_tool(story, catalog=_catalog()) is None

    def test_generic_caption_not_modified(self):
        story = _story(title="AI tutorial for beginners")
        content = _empty_content()
        baseline_caption = content["instagram"]["caption"]
        attached = apply_source_tool_disclosure(content, story, catalog=_catalog())
        assert attached is None
        assert content["instagram"]["caption"] == baseline_caption
        # No affiliate fields set on the story.
        assert "affiliate_product" not in story
        assert "affiliate_url" not in story


# ---------------------------------------------------------------------------
# Test 4 — multiple tool mentions → first match wins, only one attach
# ---------------------------------------------------------------------------


class TestMultipleToolsFirstWins:
    def test_multiple_tools_first_in_catalog_wins(self):
        # Source mentions Runway AND Midjourney. Catalog order is
        # Runway, Sora, Midjourney → Runway should win.
        story = _story(title="I combined Runway and Midjourney for this scene")
        product = detect_source_tool(story, catalog=_catalog())
        assert product is not None
        assert product["name"] == "Runway"

    def test_multiple_tools_only_one_disclosure_appended(self):
        story = _story(title="I combined Runway and Midjourney for this scene")
        content = _empty_content()
        apply_source_tool_disclosure(content, story, catalog=_catalog())
        # Exactly one "Made with " in the caption (the Runway one).
        ig = content["instagram"]["caption"]
        assert ig.lower().count("made with ") == 1
        assert "Made with Runway" in ig
        assert "Made with Midjourney" not in ig


# ---------------------------------------------------------------------------
# Test 5 — niche != ai_creators → no attach
# ---------------------------------------------------------------------------


class TestNicheScoping:
    def test_gaming_niche_in_catalog_blocks_match(self):
        # Build a catalog with the source-tool products living under
        # 'gaming' instead of 'ai_creators' — detector ignores them.
        story = _story(title="I used Runway to make this clip")
        gaming_catalog = _catalog(niche_id="gaming")
        # ai_creators key is empty → no products to scan.
        assert detect_source_tool(story, catalog=gaming_catalog) is None

    def test_product_with_other_niche_blocked(self):
        # Even when the product lives under ai_creators, if its
        # ``niches`` list excludes ai_creators, the detector skips it.
        other_niche_product = {
            **_runway_product(),
            "niches": ["gaming"],  # NOT ai_creators
        }
        catalog = {
            "niches": {"ai_creators": {"products": [other_niche_product]}},
            "settings": {},
        }
        story = _story(title="I used Runway to make this clip")
        assert detect_source_tool(story, catalog=catalog) is None


# ---------------------------------------------------------------------------
# Test 6 — already-affiliated blueprint → no duplicate
# ---------------------------------------------------------------------------


class TestNoDoubleAttach:
    def test_existing_affiliate_url_skips_attach(self):
        story = _story(title="I used Runway to make this clip")
        # Pre-set affiliate fields (upstream generic AffiliateMatch
        # already attached something).
        story["affiliate_url"] = "https://amazon.com/dp/X?tag=foo"
        story["affiliate_product"] = "Some Other Product"
        content = _empty_content()
        baseline = content["instagram"]["caption"]
        attached = apply_source_tool_disclosure(content, story, catalog=_catalog())
        assert attached is None
        # Caption unchanged.
        assert content["instagram"]["caption"] == baseline
        # Upstream affiliate fields preserved.
        assert story["affiliate_product"] == "Some Other Product"

    def test_existing_made_with_in_caption_skips_attach(self):
        story = _story(title="I used Runway to make this clip")
        content = _empty_content()
        content["instagram"]["caption"] = "wild — Made with Runway — https://x"
        attached = apply_source_tool_disclosure(content, story, catalog=_catalog())
        assert attached is None
        # Caption unchanged (no second disclosure appended).
        assert content["instagram"]["caption"].count("Made with ") == 1


# ---------------------------------------------------------------------------
# Auxiliary — build_disclosure helper
# ---------------------------------------------------------------------------


class TestBuildDisclosure:
    def test_format_substitutes_url(self):
        out = build_disclosure(_runway_product())
        assert out == "Made with Runway — https://runwayml.com/?via=genlab"

    def test_missing_template_returns_empty(self):
        prod = _runway_product()
        prod.pop("disclosure_template")
        assert build_disclosure(prod) == ""

    def test_missing_url_returns_empty(self):
        prod = _runway_product()
        prod["networks"] = {}
        assert build_disclosure(prod) == ""


# ---------------------------------------------------------------------------
# Auxiliary — search-text scope (tags + channel_name)
# ---------------------------------------------------------------------------


class TestSearchTextScope:
    def test_match_in_tags(self):
        story = _story(
            title="incredible cinematic clip",
            tags=["ai", "runway", "vfx"],
        )
        product = detect_source_tool(story, catalog=_catalog())
        assert product is not None
        assert product["name"] == "Runway"

    def test_match_in_channel_name(self):
        story = _story(
            title="my morning ride",
            channel_name="Sora Official",
        )
        product = detect_source_tool(story, catalog=_catalog())
        assert product is not None
        assert product["name"] == "Sora"

    def test_disabled_product_skipped(self):
        prod = _runway_product()
        prod["enabled"] = False
        catalog = {
            "niches": {"ai_creators": {"products": [prod]}},
            "settings": {},
        }
        story = _story(title="I used Runway to make this clip")
        assert detect_source_tool(story, catalog=catalog) is None

    def test_word_boundary_avoids_substring_false_positive(self):
        # "runaway" should NOT match "runway".
        story = _story(title="The runaway success of this AI startup")
        assert detect_source_tool(story, catalog=_catalog()) is None
