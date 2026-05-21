"""Tests for ClutchWire hook generation strategy execution."""

import pytest
from cw_strategies.hooks import SportHookStrategy


@pytest.fixture
def strategy():
    return SportHookStrategy()


def _make_story(**kwargs):
    base = {
        "title": "Lakers beat Celtics",
        "summary": "An incredible game",
        "teams": ["Lakers", "Celtics"],
        "players": ["LeBron"],
        "sport": "basketball",
        "league": "nba",
    }
    base.update(kwargs)
    return base


class TestHookClassification:
    def test_upset_classified(self, strategy):
        story = _make_story(is_upset=True)
        assert strategy._classify_story(story) == "upset"

    def test_record_classified(self, strategy):
        story = _make_story(is_record=True)
        assert strategy._classify_story(story) == "record"

    def test_injury_classified(self, strategy):
        story = _make_story(title="LeBron injured — out for the season")
        assert strategy._classify_story(story) == "injury"

    def test_trade_classified(self, strategy):
        story = _make_story(title="Lakers trade for Anthony Davis")
        assert strategy._classify_story(story) == "trade"

    def test_clutch_classified(self, strategy):
        story = _make_story(title="Incredible buzzer beater wins the game")
        assert strategy._classify_story(story) == "clutch"

    def test_default_fallback(self, strategy):
        story = _make_story(title="Regular season recap")
        assert strategy._classify_story(story) == "default"


class TestHookGeneration:
    def test_hook_generated(self, strategy):
        strategy._ensure_config()
        story = _make_story()
        hook = strategy._generate_hook(story)
        assert len(hook) > 0
        assert "{team}" not in hook  # Placeholders should be substituted

    def test_team_substituted(self, strategy):
        strategy._ensure_config()
        story = _make_story(is_upset=True)
        hook = strategy._generate_hook(story)
        # "Lakers" or "them" should appear, not {team}
        assert "{team}" not in hook

    def test_forbidden_styles_stripped(self, strategy):
        strategy._ensure_config()
        story = _make_story()
        hook = strategy._generate_hook(story)
        assert not hook.startswith("BREAKING:")
        assert not hook.startswith("JUST IN:")


class TestHookExecute:
    def test_execute_hooks_all_stories(self, strategy):
        ctx = {
            "stories": [
                _make_story(),
                _make_story(title="Warriors win", teams=["Warriors"]),
            ]
        }
        result = strategy.execute(ctx)
        assert result["run_stats"]["hooks"]["hooked_count"] == 2
        for story in result["stories"]:
            assert "hook" in story.get("content", {})
            assert "hook_category" in story.get("content", {})

    def test_execute_empty_stories(self, strategy):
        ctx = {"stories": []}
        result = strategy.execute(ctx)
        assert result["run_stats"]["hooks"]["hooked_count"] == 0

    def test_categories_tracked(self, strategy):
        ctx = {
            "stories": [
                _make_story(is_upset=True),
                _make_story(is_record=True),
                _make_story(),
            ]
        }
        result = strategy.execute(ctx)
        cats = result["run_stats"]["hooks"]["categories_used"]
        assert "upset" in cats
        assert "record" in cats


class TestHookDedup:
    """Cross-story deduplication and 60-char cap."""

    def test_no_duplicate_hooks_across_stories(self, strategy):
        # 7 identical stories should produce 7 unique hooks
        ctx = {"stories": [_make_story() for _ in range(7)]}
        result = strategy.execute(ctx)
        hooks = [s["content"]["hook"] for s in result["stories"]]
        assert len(hooks) == len(set(h.lower() for h in hooks))

    def test_hooks_capped_at_60_chars(self, strategy):
        ctx = {"stories": [_make_story() for _ in range(5)]}
        result = strategy.execute(ctx)
        for story in result["stories"]:
            assert len(story["content"]["hook"]) <= 60

    def test_dedup_falls_back_to_title(self, strategy):
        # With enough stories, should eventually fall back to title-derived hooks
        ctx = {"stories": [_make_story(title=f"Story {i}") for i in range(20)]}
        result = strategy.execute(ctx)
        hooks = [s["content"]["hook"] for s in result["stories"]]
        assert len(hooks) == len(set(h.lower() for h in hooks))


class TestHookLLMSkip:
    """Hooks strategy skips stories that already have LLM-generated hooks."""

    def test_skips_llm_hook_stories(self, strategy):
        stories = [
            {**_make_story(), "content": {"hook": "LLM wrote this", "written_by": "llm"}},
            _make_story(title="Warriors win", teams=["Warriors"]),
        ]
        ctx = {"stories": stories}
        result = strategy.execute(ctx)
        assert result["run_stats"]["hooks"]["skipped_llm"] == 1
        assert result["run_stats"]["hooks"]["hooked_count"] == 1
        # First story's LLM hook should be preserved
        assert stories[0]["content"]["hook"] == "LLM wrote this"

    def test_does_not_skip_template_hook(self, strategy):
        """Template-written hooks (no written_by=llm) should be overwritten."""
        stories = [
            {**_make_story(), "content": {"hook": "Template hook", "written": True}},
        ]
        ctx = {"stories": stories}
        result = strategy.execute(ctx)
        assert result["run_stats"]["hooks"]["skipped_llm"] == 0
        assert result["run_stats"]["hooks"]["hooked_count"] == 1

    def test_all_llm_stories_skipped(self, strategy):
        stories = [
            {**_make_story(), "content": {"hook": "LLM hook 1", "written_by": "llm"}},
            {
                **_make_story(title="Story 2"),
                "content": {"hook": "LLM hook 2", "written_by": "llm"},
            },
        ]
        ctx = {"stories": stories}
        result = strategy.execute(ctx)
        assert result["run_stats"]["hooks"]["skipped_llm"] == 2
        assert result["run_stats"]["hooks"]["hooked_count"] == 0

    def test_llm_hooks_added_to_used_set_for_dedup(self, strategy):
        """LLM hooks should be tracked so template hooks don't duplicate them."""
        stories = [
            {**_make_story(), "content": {"hook": "Lakers fans right now", "written_by": "llm"}},
            _make_story(title="Story 2", teams=["Lakers"]),
        ]
        ctx = {"stories": stories}
        strategy.execute(ctx)
        hooks = [s["content"]["hook"] for s in stories]
        # All hooks should be unique (case-insensitive)
        assert len(set(h.lower() for h in hooks)) == len(hooks)
