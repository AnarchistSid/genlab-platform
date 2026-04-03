"""Tests for FrameDrift hook generation strategy."""

from unittest.mock import patch

import pytest
from fd_strategies.hooks import AnimeHookStrategy


@pytest.fixture
def strategy(tmp_path):
    import yaml
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "templates.yaml").write_text(yaml.dump({
        "hooks": {
            "formulas": ["This anime is about to blow up"],
            "forbidden_styles": ["BREAKING:", "JUST IN:"],
            "story_categories": {
                "anime_premiere": {
                    "formulas": ["{title} just dropped and it's PEAK"],
                    "weight": 1.3,
                },
                "voice_actor_trigger": {
                    "formulas": ["{voice_actor} just made {item} the most hyped anime"],
                    "weight": 1.4,
                },
                "trend_emerging": {
                    "formulas": ["The {trend_name} trend is quietly taking over"],
                    "weight": 1.0,
                },
                "studio_collab": {
                    "formulas": ["The {studio1} x {studio2} collab is everything"],
                    "weight": 1.2,
                },
                "manga_release": {
                    "formulas": ["{title}'s latest chapter just dropped"],
                    "weight": 1.1,
                },
                "default": {
                    "formulas": ["Must watch"],
                    "weight": 1.0,
                },
            },
        },
    }))
    with patch("fd_strategies.hooks.NICHE_ROOT", tmp_path):
        s = AnimeHookStrategy()
        s._ensure_config()
        yield s


class TestHookClassification:
    def test_creator_hook_selected_for_creator_spotlight(self, strategy):
        story = {"title": "Test", "is_creator_spotlight": True, "trend_cycle_stage": "peak"}
        cat = strategy._classify_story(story)
        assert cat == "voice_actor_trigger"

    def test_release_hook_selected_for_new_release(self, strategy):
        story = {"title": "Test", "is_new_release": True, "trend_cycle_stage": "emerging"}
        cat = strategy._classify_story(story)
        assert cat == "anime_premiere"

    def test_emerging_cycle_uses_trend_emerging_hook(self, strategy):
        story = {"title": "Test", "trend_cycle_stage": "emerging"}
        cat = strategy._classify_story(story)
        assert cat == "trend_emerging"

    def test_collab_classified(self, strategy):
        story = {"title": "Test", "is_collab": True, "trend_cycle_stage": "peak"}
        cat = strategy._classify_story(story)
        assert cat == "studio_collab"

    def test_creator_takes_priority_over_release(self, strategy):
        story = {"title": "Test", "is_creator_spotlight": True, "is_new_release": True}
        cat = strategy._classify_story(story)
        assert cat == "voice_actor_trigger"


class TestHookGeneration:
    @patch("genlab_core.writing.llm_hook_generator.generate_hook", return_value=None)
    def test_hook_substitutes_trend_name(self, _mock_llm, strategy):
        story = {
            "title": "Test",
            "trend_name": "isekai revival",
            "trend_cycle_stage": "emerging",
        }
        hook = strategy._generate_hook(story)
        assert "isekai revival" in hook

    def test_execute_hooks_all_stories(self, strategy):
        context = {
            "stories": [
                {"title": "Premiere A", "is_new_release": True, "trend_cycle_stage": "peak"},
                {"title": "Creator B", "is_creator_spotlight": True, "trend_cycle_stage": "peak"},
            ]
        }
        result = strategy.execute(context)
        for story in result["stories"]:
            assert "hook" in story.get("content", {})
        assert result["run_stats"]["hooks"]["hooked_count"] == 2


class TestPlaceholderTruncation:
    def test_placeholder_truncates_long_trend_name_at_word_boundary(self, strategy):
        story = {
            "title": "Test",
            "trend_name": "the isekai revival aesthetic is redefining modern anime globally",
            "trend_cycle_stage": "emerging",
        }
        hook = strategy._generate_hook(story)
        # Should not end with a truncated word or dangling punctuation
        # (ellipsis "..." from 60-char cap is acceptable)
        assert not hook.endswith(".") or hook.endswith("...")
        assert not hook.endswith(",")
        assert len(hook) <= 60

    def test_placeholder_removes_unreplaced_variables(self, strategy):
        result = strategy._substitute_placeholders(
            "Check {nonexistent_var} now",
            {"title": "Test"},
        )
        assert "{" not in result
        assert "}" not in result

    def test_placeholder_no_trailing_punctuation_after_truncation(self, strategy):
        result = strategy._substitute_placeholders(
            "{trend_name} is here",
            {
                "title": "Test",
                "trend_name": "A very long trend name that exceeds the forty character limit definitely",
            },
        )
        # Should not have trailing punctuation before " is here"
        assert ".  " not in result
        assert ",  " not in result


class TestHookDedup:
    """Cross-story deduplication and 60-char cap."""

    def test_no_duplicate_hooks_across_stories(self, strategy):
        ctx = {
            "stories": [
                {"title": "Trend A", "trend_cycle_stage": "emerging", "trend_name": "isekai revival"}
                for _ in range(5)
            ]
        }
        result = strategy.execute(ctx)
        hooks = [s["content"]["hook"] for s in result["stories"]]
        assert len(hooks) == len(set(h.lower() for h in hooks))

    def test_hooks_capped_at_60_chars(self, strategy):
        ctx = {
            "stories": [
                {"title": f"Story {i}", "trend_cycle_stage": "unknown"}
                for i in range(5)
            ]
        }
        result = strategy.execute(ctx)
        for story in result["stories"]:
            assert len(story["content"]["hook"]) <= 60

    def test_dedup_falls_back_to_title(self, strategy):
        ctx = {
            "stories": [
                {"title": f"Anime news {i}", "trend_cycle_stage": "unknown"}
                for i in range(10)
            ]
        }
        result = strategy.execute(ctx)
        hooks = [s["content"]["hook"] for s in result["stories"]]
        assert len(hooks) == len(set(h.lower() for h in hooks))


class TestHookLLMSkip:
    """Hooks strategy skips stories that already have LLM-generated hooks."""

    def test_skips_llm_hook_stories(self, strategy):
        stories = [
            {"title": "Anime A", "trend_cycle_stage": "peak",
             "content": {"hook": "LLM anime hook", "written_by": "llm"}},
            {"title": "Anime B", "trend_cycle_stage": "emerging", "trend_name": "isekai revival"},
        ]
        ctx = {"stories": stories}
        result = strategy.execute(ctx)
        assert result["run_stats"]["hooks"]["skipped_llm"] == 1
        assert result["run_stats"]["hooks"]["hooked_count"] == 1
        assert stories[0]["content"]["hook"] == "LLM anime hook"

    def test_does_not_skip_template_hook(self, strategy):
        stories = [
            {"title": "Anime A", "trend_cycle_stage": "peak",
             "content": {"hook": "Template hook", "written": True}},
        ]
        ctx = {"stories": stories}
        result = strategy.execute(ctx)
        assert result["run_stats"]["hooks"]["skipped_llm"] == 0
        assert result["run_stats"]["hooks"]["hooked_count"] == 1

    def test_all_llm_stories_skipped(self, strategy):
        stories = [
            {"title": f"Anime {i}", "trend_cycle_stage": "peak",
             "content": {"hook": f"LLM hook {i}", "written_by": "llm"}}
            for i in range(3)
        ]
        ctx = {"stories": stories}
        result = strategy.execute(ctx)
        assert result["run_stats"]["hooks"]["skipped_llm"] == 3
        assert result["run_stats"]["hooks"]["hooked_count"] == 0
