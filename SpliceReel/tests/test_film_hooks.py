"""Tests for SpliceReel hook generation strategy."""

from unittest.mock import patch

import pytest
from sr_strategies.hooks import MovieHookStrategy


@pytest.fixture
def strategy(tmp_path):
    """Create strategy with test templates config."""
    import yaml

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "templates.yaml").write_text(
        yaml.dump(
            {
                "hooks": {
                    "formulas": [
                        "This scene alone is worth the ticket",
                    ],
                    "forbidden_styles": ["BREAKING:", "JUST IN:"],
                    "story_categories": {
                        "trailer_drop": {
                            # Must be a formula that passes the banned-pattern
                            # filter — "...trailer just dropped and I'm not okay"
                            # trips an emotional-reaction ban, which made this
                            # test fall through to random other-category
                            # formulas (flaky ~80% fail).
                            "formulas": [
                                "The first {film_title} trailer changes everything",
                            ],
                            "weight": 1.3,
                        },
                        "box_office_win": {
                            "formulas": [
                                "{film_title} delivered at the box office",
                            ],
                            "weight": 1.2,
                        },
                        "award_nomination": {
                            "formulas": [
                                "{film_title} just picked up an Oscar nomination. Deserved?",
                            ],
                            "weight": 1.3,
                        },
                        "franchise_news": {
                            "formulas": [
                                "The {franchise} universe will never be the same after this",
                            ],
                            "weight": 1.2,
                        },
                        "streaming_premiere": {
                            "formulas": [
                                "{film_title} just landed on streaming. Clear your weekend",
                            ],
                            "weight": 1.1,
                        },
                        "default": {
                            "formulas": ["Cinema is back"],
                            "weight": 1.0,
                        },
                    },
                },
            }
        )
    )
    with patch("sr_strategies.hooks.NICHE_ROOT", tmp_path):
        s = MovieHookStrategy()
        s._ensure_config()
        yield s


class TestHookClassification:
    def test_trailer_classified_for_trailer_drop(self, strategy):
        story = {"title": "Test", "is_trailer_drop": True, "lifecycle_stage": "pre_release"}
        cat = strategy._classify_story(story)
        assert cat == "trailer_drop"

    def test_box_office_classified_for_opening_weekend(self, strategy):
        story = {"title": "Test", "is_box_office_news": True, "lifecycle_stage": "opening_weekend"}
        cat = strategy._classify_story(story)
        assert cat == "box_office_win"

    def test_award_news_classified(self, strategy):
        story = {"title": "Test", "is_award_news": True, "lifecycle_stage": "long_tail"}
        cat = strategy._classify_story(story)
        assert cat == "award_nomination"

    def test_franchise_pre_release_classified(self, strategy):
        story = {"title": "Test", "franchise": "MCU", "lifecycle_stage": "pre_release"}
        cat = strategy._classify_story(story)
        assert cat == "franchise_news"

    def test_streaming_keyword_detected(self, strategy):
        story = {
            "title": "New film arrives on Netflix this week",
            "summary": "",
            "lifecycle_stage": "long_tail",
        }
        cat = strategy._classify_story(story)
        assert cat == "streaming_premiere"


class TestHookGeneration:
    @patch("sr_strategies.hooks.generate_hook", return_value=None, create=True)
    def test_trailer_hook_substitutes_film_title(self, _mock_llm, strategy):
        with patch("genlab_core.writing.llm_hook_generator.generate_hook", return_value=None):
            story = {
                "title": "Test",
                "film_title": "Thunderbolts",
                "is_trailer_drop": True,
                "lifecycle_stage": "pre_release",
            }
            hook = strategy._generate_hook(story)
            assert "Thunderbolts" in hook

    @patch("genlab_core.writing.llm_hook_generator.generate_hook", return_value=None)
    def test_franchise_hook_substitutes_franchise_name(self, _mock_llm, strategy):
        story = {
            "title": "Test",
            "film_title": "Avengers 6",
            "franchise": "MCU",
            "lifecycle_stage": "pre_release",
        }
        hook = strategy._generate_hook(story)
        assert "MCU" in hook

    def test_forbidden_style_stripped(self, strategy):
        story = {"title": "BREAKING: Some Film News", "lifecycle_stage": "unknown"}
        hook = strategy._generate_hook(story)
        assert not hook.upper().startswith("BREAKING:")


class TestHookExecute:
    def test_execute_hooks_all_stories(self, strategy):
        context = {
            "stories": [
                {"title": "Film A", "film_title": "Film A", "lifecycle_stage": "opening_weekend"},
                {
                    "title": "Film B",
                    "film_title": "Film B",
                    "is_trailer_drop": True,
                    "lifecycle_stage": "pre_release",
                },
            ]
        }
        result = strategy.execute(context)
        for story in result["stories"]:
            assert "hook" in story.get("content", {})
            assert "hook_category" in story.get("content", {})
        assert result["run_stats"]["hooks"]["hooked_count"] == 2


class TestHookDedup:
    """Cross-story deduplication and 60-char cap."""

    def test_no_duplicate_hooks_across_stories(self, strategy):
        ctx = {
            "stories": [
                {"title": "Film A", "film_title": "Film A", "lifecycle_stage": "opening_weekend"}
                for _ in range(5)
            ]
        }
        result = strategy.execute(ctx)
        hooks = [s["content"]["hook"] for s in result["stories"]]
        assert len(hooks) == len(set(h.lower() for h in hooks))

    def test_hooks_capped_at_60_chars(self, strategy):
        ctx = {
            "stories": [
                {
                    "title": f"Film {i}",
                    "film_title": f"A Very Long Film Title {i}",
                    "lifecycle_stage": "unknown",
                }
                for i in range(5)
            ]
        }
        result = strategy.execute(ctx)
        for story in result["stories"]:
            assert len(story["content"]["hook"]) <= 60

    def test_dedup_falls_back_to_title(self, strategy):
        ctx = {
            "stories": [
                {"title": f"Movie {i}", "film_title": f"Movie {i}", "lifecycle_stage": "unknown"}
                for i in range(10)
            ]
        }
        result = strategy.execute(ctx)
        hooks = [s["content"]["hook"] for s in result["stories"]]
        assert len(hooks) == len(set(h.lower() for h in hooks))


class TestHookLLMSkip:
    """Hooks strategy skips stories that already have LLM-generated hooks."""

    def test_skips_llm_hook_stories(self, strategy):
        # The LLM hook must be a *valid* hook (≥3 words, ≤60 chars) — R-52 now
        # validates LLM hooks too, so a 2-word placeholder would be rejected and
        # regenerated rather than skipped.
        llm_hook = "Film A just broke a wild box office record"
        stories = [
            {
                "title": "Film A",
                "film_title": "Film A",
                "lifecycle_stage": "unknown",
                "content": {"hook": llm_hook, "written_by": "llm"},
            },
            {"title": "Film B", "film_title": "Film B", "lifecycle_stage": "opening_weekend"},
        ]
        ctx = {"stories": stories}
        result = strategy.execute(ctx)
        assert result["run_stats"]["hooks"]["skipped_llm"] == 1
        assert result["run_stats"]["hooks"]["hooked_count"] == 1
        assert stories[0]["content"]["hook"] == llm_hook

    def test_does_not_skip_template_hook(self, strategy):
        stories = [
            {
                "title": "Film A",
                "film_title": "Film A",
                "lifecycle_stage": "unknown",
                "content": {"hook": "Template hook", "written": True},
            },
        ]
        ctx = {"stories": stories}
        result = strategy.execute(ctx)
        assert result["run_stats"]["hooks"]["skipped_llm"] == 0
        assert result["run_stats"]["hooks"]["hooked_count"] == 1

    def test_all_llm_stories_skipped(self, strategy):
        stories = [
            {
                "title": f"Film {i}",
                "film_title": f"Film {i}",
                "lifecycle_stage": "unknown",
                "content": {"hook": f"LLM hook {i}", "written_by": "llm"},
            }
            for i in range(3)
        ]
        ctx = {"stories": stories}
        result = strategy.execute(ctx)
        assert result["run_stats"]["hooks"]["skipped_llm"] == 3
        assert result["run_stats"]["hooks"]["hooked_count"] == 0
