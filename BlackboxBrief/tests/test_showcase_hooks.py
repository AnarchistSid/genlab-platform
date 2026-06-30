"""Phase 2 C1 (2026-06-30) — showcase hook template pins.

BlackboxBrief sources with ``content_type: showcase`` in sources.yaml
should produce visual-first hooks ("Watch this 30s Sora video",
"Spot the difference: real vs AI", etc.) NOT news-style hooks
("OpenAI released X"). Pre-C1, the classifier only looked at title
keywords + a source-declared content_type would fall through to
``creator_showcase`` or other text-based categories, leading to
news-toned hooks on creator-showcase reels.

This module pins:
  * _classify_story prefers content_type=showcase over title-keyword
    classifiers
  * News stories still get news-style categories (no regression)
  * The new showcase formulas are present in templates.yaml
  * The new {duration}/{tool}/{output_type} placeholders resolve
    against story metadata
  * Substitution leaves no unfilled `{...}` literals
"""

from __future__ import annotations

import pytest

from bb_strategies.hooks import (
    BBHookStrategy,
    _extract_content_type,
    _extract_duration,
    _extract_output_type,
    _extract_tool,
)


@pytest.fixture
def strategy():
    return BBHookStrategy()


# ── _classify_story content_type precedence ───────────────────────


def test_showcase_content_type_returns_showcase_category(strategy):
    """Top-level content_type='showcase' wins over title-keyword
    classification. A news-style title with showcase content_type
    should STILL classify as showcase."""
    story = {
        "title": "OpenAI released GPT-5",  # tool_launch keywords ('release')
        "content_type": "showcase",
    }
    assert strategy._classify_story(story) == "showcase"


def test_showcase_content_type_in_gallery_metadata_wins(strategy):
    """Pixwith / gallery scrapers nest content_type — must be read."""
    story = {
        "title": "An interesting AI release",
        "gallery_metadata": {"content_type": "showcase"},
    }
    assert strategy._classify_story(story) == "showcase"


def test_showcase_content_type_in_source_config_wins(strategy):
    """Source-config metadata path is the third valid location."""
    story = {
        "title": "Big launch news today",
        "source_config": {"content_type": "showcase"},
    }
    assert strategy._classify_story(story) == "showcase"


def test_news_story_still_classifies_as_tool_launch(strategy):
    """Pin no-regression: news story (no content_type) still routes
    through the text-keyword classifier."""
    story = {"title": "OpenAI released GPT-5", "summary": "Next-gen model"}
    assert strategy._classify_story(story) == "tool_launch"


def test_news_story_still_classifies_as_creator_showcase(strategy):
    """Pin no-regression: title keywords ('made', 'created') still
    map to creator_showcase when content_type is NOT showcase."""
    story = {
        "title": "A creator made a cool render",
        "content_type": "news",
    }
    assert strategy._classify_story(story) == "creator_showcase"


def test_empty_content_type_falls_through_to_text_classifier(strategy):
    story = {"title": "OpenAI released GPT-5", "content_type": ""}
    assert strategy._classify_story(story) == "tool_launch"


# ── templates.yaml showcase category present ──────────────────────


def test_showcase_category_present_in_templates(strategy):
    """The new 'showcase' category MUST be in templates.yaml with
    at least 5 formulas (per task spec)."""
    strategy._ensure_config()
    hooks = (strategy._templates or {}).get("hooks", {})
    cats = hooks.get("story_categories", {})
    assert "showcase" in cats, (
        "Missing 'showcase' category in templates.yaml — Phase 2 C1 needs visual-first hook templates"
    )
    formulas = cats["showcase"].get("formulas", [])
    assert len(formulas) >= 5, f"Expected ≥5 showcase formulas; got {len(formulas)}: {formulas}"


def test_showcase_templates_are_visual_first(strategy):
    """Sanity: showcase formulas should contain visual-cue words
    ('watch', 'spot', 'see', 'AI made') — not news verbs like
    'released' or 'announced'."""
    strategy._ensure_config()
    hooks = (strategy._templates or {}).get("hooks", {})
    formulas = hooks.get("story_categories", {}).get("showcase", {}).get("formulas", [])
    joined = " ".join(formulas).lower()
    visual_hits = sum(1 for kw in ("watch", "spot", "see", "AI made", "spot the difference") if kw in joined)
    assert visual_hits >= 2, f"Showcase formulas should be visual-first; got: {formulas}"
    news_hits = sum(1 for kw in ("released", "announced", "drops", "launched", "ceo said") if kw in joined)
    assert news_hits == 0, f"Showcase formulas must not be news-toned; found news verbs in: {formulas}"


# ── placeholder extractors ────────────────────────────────────────


class TestExtractContentType:
    def test_top_level_wins(self):
        assert _extract_content_type({"content_type": "showcase"}) == "showcase"

    def test_case_insensitive(self):
        assert _extract_content_type({"content_type": "SHOWCASE"}) == "showcase"

    def test_whitespace_trimmed(self):
        assert _extract_content_type({"content_type": "  showcase  "}) == "showcase"

    def test_unset_returns_empty(self):
        assert _extract_content_type({}) == ""

    def test_non_string_returns_empty(self):
        assert _extract_content_type({"content_type": None}) == ""
        assert _extract_content_type({"content_type": 42}) == ""


class TestExtractDuration:
    def test_rounds_to_nearest_5_seconds(self):
        # 27s should round to 25 (nearest 5)
        assert _extract_duration({"duration_seconds": 27}) == "25"

    def test_30s_stays_30(self):
        assert _extract_duration({"duration_seconds": 30}) == "30"

    def test_missing_defaults_to_30(self):
        assert _extract_duration({}) == "30"

    def test_zero_falls_through_to_default(self):
        assert _extract_duration({"duration_seconds": 0}) == "30"

    def test_float_works(self):
        assert _extract_duration({"duration_seconds": 32.5}) == "30"


class TestExtractTool:
    def test_product_wins(self):
        # When product is extracted, it's preferred
        assert _extract_tool({}, product="Sora", company="OpenAI") == "Sora"

    def test_company_when_no_product(self):
        assert _extract_tool({}, product="", company="OpenAI") == "OpenAI"

    def test_gallery_model_when_no_product_no_company(self):
        story = {"gallery_metadata": {"model": "Midjourney V6"}}
        assert _extract_tool(story, product="", company="") == "Midjourney V6"

    def test_ai_fallback(self):
        assert _extract_tool({}, product="", company="") == "AI"


class TestExtractOutputType:
    def test_video_from_title(self):
        assert _extract_output_type({}, "Watch this AI video") == "video"

    def test_image_from_title(self):
        assert _extract_output_type({}, "An incredible AI image render") == "image"

    def test_song_from_title(self):
        assert _extract_output_type({}, "A song generated by AI") == "song"

    def test_gallery_metadata_content_type(self):
        story = {"gallery_metadata": {"content_type": "ai_video"}}
        assert _extract_output_type(story, "Some title") == "video"

    def test_unknown_falls_back_to_output(self):
        assert _extract_output_type({}, "Some completely generic title") == "output"


# ── end-to-end substitution: no leftover placeholders ─────────────


def test_showcase_substitution_resolves_all_placeholders(strategy):
    """When a showcase formula is substituted against a complete story,
    NO `{...}` literal should remain in the result (otherwise the
    template is silently discarded by BaseHookStrategy._generate_hook
    via the 'if not hook: continue' path)."""
    story = {
        "title": "Sora generated this incredible AI video",
        "content_type": "showcase",
        "duration_seconds": 30,
        "gallery_metadata": {"model": "Sora", "content_type": "ai_video"},
    }
    strategy._ensure_config()
    hooks = (strategy._templates or {}).get("hooks", {})
    formulas = hooks.get("story_categories", {}).get("showcase", {}).get("formulas", [])
    # Try each formula; track which ones produced a populated hook
    resolved_any = False
    for formula in formulas:
        result = strategy._substitute_placeholders(formula, story)
        if result:
            resolved_any = True
            # Pin: no leftover {placeholder} literals
            assert "{" not in result and "}" not in result, (
                f"Showcase formula {formula!r} produced output with unfilled placeholders: {result!r}"
            )
    assert resolved_any, (
        "No showcase formula produced a populated hook — the new "
        "placeholders ({duration}/{tool}/{output_type}) aren't "
        "resolving against a story with full metadata."
    )


def test_substitute_handles_cold_start_story_gracefully(strategy):
    """Even with a near-empty story (no product, no duration, no
    gallery_metadata), the safe-fallback placeholders ({duration}=30,
    {tool}=AI, {output_type}=output) should still resolve, so some
    formulas survive substitution."""
    story = {"title": "An AI demo", "content_type": "showcase"}
    strategy._ensure_config()
    hooks = (strategy._templates or {}).get("hooks", {})
    formulas = hooks.get("story_categories", {}).get("showcase", {}).get("formulas", [])
    # At least one formula should resolve via the safe fallbacks
    # (those that don't need {product}/{topic})
    resolved = [strategy._substitute_placeholders(f, story) for f in formulas]
    populated = [r for r in resolved if r]
    assert len(populated) >= 1, f"At least one showcase formula must resolve via safe fallbacks; got: {resolved}"
